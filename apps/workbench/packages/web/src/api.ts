import type { PreviewNote, PreviewPick } from './preview-notes';
import type { ContextReference } from './context-references';
import type { GitStatus, GitLog, GitCommitFiles, GitDiff, GitOperation } from '../../daemon/src/git';
export type { GitStatus, GitLog, GitCommitFiles, GitDiff, GitOperation, GitFile, GitCommit } from '../../daemon/src/git';
import type { ExtensionState, Permission, ExtensionPackagePreview, ExtensionConfiguration, ExtensionDetection, WorkProjection, ProgressReport } from '@calli/extension-api';
export type WorkMode = 'execute' | 'plan';
export type AutoArchiveDays = 1 | 3 | 7 | 15 | 30 | null;
export interface GeneralSettings {
  /** Shared startup preference stored by the daemon. */
  startupPage: 'new-thread' | 'recent-thread';
  /** Shared automatic archive window. Null means conversations stay in the sidebar. */
  autoArchiveDays: AutoArchiveDays;
  /** List every conversation the agents on this machine already have; off: only LoopX Goal conversations. */
  autoImportLocal: boolean;
}
import type { DraftModelPreference, ModelPreferencePatch } from '../../daemon/src/model-preferences';
export type { DraftModelPreference } from '../../daemon/src/model-preferences';
export interface UserQuestion { id: string; question: string; header?: string; options: Array<{ value: string; label: string; description?: string }>; multiple?: boolean; freeText?: boolean; required?: boolean }
export type UserInputAnswer = { answers: Record<string, string[]> } | { cancelled: true };
import type { UsageReport, AgentDiagnostic, CostReport, DailyUsage } from './observability';
import type { StoredEvent } from './timeline';
import type { DetailRef } from './timeline';
import type { HistoryPage, HistoryDetail, HistoryWork } from './history';
import { cachedQuota, clearQuotaCache, rememberQuota } from './quota-cache';
import { clearThreadCache } from './thread-cache';
import { clearHostCache } from './host-cache';
import { localeTag, t } from './i18n';
import { isSupportedVideo } from './videos';

/** Where threads may work: the project roots, and whether one can start outside any project. */
export interface FolderEntry {
  name: string;
  type: 'dir' | 'file';
  size?: number;
  /** Ignored by git: shown, but quieter. */
  ignored?: boolean;
}

/** A folder as the file browser sees it. `root` is the real path of the thread folder it is in. */
export interface FolderListing {
  root: string;
  path: string;
  entries: FolderEntry[];
  truncated: boolean;
}

export interface TextFile {
  root: string;
  path: string;
  size: number;
  /** Null for a binary file. */
  text: string | null;
  /** Only the start of a long file is in `text`. */
  truncated: boolean;
  /** SHA-256 of the whole file, sent back when saving; null for a binary or truncated file. Missing from older daemons. */
  hash?: string | null;
  /** A writing-mode body (a `.md` under the thread's `writing/`) that the page may save. */
  editable?: boolean;
}

/** The steps of writing mode, in order: first draft, second, third, figures, polish. */
export type WritingStage = 'draft' | 'second' | 'third' | 'figures' | 'polish';
/** `file`: the body, relative to the thread's directory; null until the agent has written one. */
export interface WritingState {
  stage: WritingStage;
  file: string | null;
  /** The person's writing habits note, kept outside the thread's folder; may not exist yet.
   * Missing from older daemons. */
  habits?: string | null;
}

export interface WorkdirsConfig {
  workdirs: string[];
  /** Each root and whether it was added from the page (and so can be removed there). Missing from older daemons. */
  roots?: Array<{ path: string; removable: boolean }>;
  /** Missing from older daemons: no threads outside a project. */
  scratch?: boolean;
}

export interface AgentMention { id: string; label: string; kind: 'agent' | 'member'; cwd?: string; /** What the user asks of this one alone. */ task?: string;
  /** An instance on another machine: its id there is `agent@host`, and its folders are that machine's. */ host?: string; directories?: string[];
  /** For an instance on another machine: its own name and the machine's, which `label` joins for the protocol line. */ name?: string; machine?: string }
/** One turn a search found: where it is, who said it, and the words around the match. */
export interface SearchHit {
  threadId: string;
  turnId: string;
  role: 'user' | 'assistant';
  ts: string;
  snippet: string;
  title: string;
  agent: string;
  cwd: string;
  archived: boolean;
}

/** The whole prompt a conversation runs with, as the agent itself persisted it. */
export interface SystemPrompt {
  text: string;
  source: string;
}

/** One day of the digest: what the user asked and what the models called. */
export interface ActivityDay {
  /** Local calendar day, `YYYY-MM-DD`. */
  date: string;
  turns: number;
  threads: number;
  prompts: Array<{ threadId: string; turnId: string; ts: string; text: string; title: string; agent: string; cwd: string; archived: boolean; /** Tool calls the turn made; absent from older daemons. */ tools?: number }>;
  morePrompts: number;
  tools: Array<{ name: string; count: number; mcp: boolean; server?: string }>;
  mcp: Array<{ server: string; count: number }>;
}

export interface ActivityReport {
  days: ActivityDay[];
}

export type TraceStepKind = 'user' | 'thinking' | 'assistant' | 'tool' | 'request' | 'context' | 'error';

/** One thing that happened in a turn, as the daemon's trace reads it out of the event log. */
export interface TraceStep {
  kind: TraceStepKind;
  ts: string;
  endTs?: string;
  text?: string;
  tool?: string;
  title?: string;
  server?: string;
  status?: string;
  input?: string;
  output?: string;
  model?: string;
  tokens?: { input?: number; output?: number; cacheRead?: number; cacheWrite?: number };
  /** A text above was cut; the thread holds all of it. */
  cut?: boolean;
}

/** One turn read back step by step: the ask, the thinking, each tool and model request, the answer. */
export interface TurnTrace {
  threadId: string;
  turnId: string;
  title: string;
  agent: string;
  cwd: string;
  models: string[];
  status: 'running' | 'done' | 'cancelled' | 'error';
  startedAt: string;
  endedAt: string | null;
  steps: TraceStep[];
  /** The turn's model segments, in order; the timeline draws one bar per segment and per tool. Missing from older daemons. */
  segments?: TraceSegment[];
  totals: { tools: number; mcp: number; requests: number; input: number; output: number };
  turns: Array<{ turnId: string; ts: string }>;
}

/** One model request's time: native bounds when the agent reported them, else the hub's reading. */
export interface TraceSegment {
  start: string;
  firstOutput: string | null;
  end: string | null;
  source: 'native' | 'hub';
  outputTokens?: number;
  /** The trace steps (1-based) whose events fall inside this segment. */
  steps: number[];
}

/** A thread's session statistics: what the top-bar readings and the token popover show. */
export interface ThreadStats {
  threadId: string;
  checkedAt: string;
  running: boolean;
  turns: number;
  steps: number;
  timing: null | {
    source: 'native' | 'hub';
    turns: number;
    segments: number;
    waitMs: number;
    avgWaitMs: number | null;
    genMs: number;
    modelMs: number;
    toolMs: number;
  };
  tokens: null | {
    turnsWithout: number;
    input: number | null;
    cacheRead: number | null;
    cacheWrite: number | null;
    output: number | null;
    reasoning: number | null;
    total: number | null;
  };
  tokPerSec: number | null;
  tokPerSecApprox: boolean;
  /** Compactions the agent itself completed; absent when an older daemon does not report it. */
  compactions?: { count: number; last: { ts: string; automatic: boolean } | null };
}

/** The model and thinking effort members of one instance start with; null leaves the agent's own. */
export interface MemberDefault { model: string | null; effort: string | null; /** Set where this instance's members differ from the team's level. */ permission?: string }
export type TeamStatus = 'running' | 'awaiting_confirm' | 'needs_attention' | 'done';
export interface TeamSummary { id: string; name: string; description?: string; entry: string; memberCount: number; entryAgent?: string; entryModel?: string | null }
export interface TeamMember { name: string; agent: string; model?: string; effort?: string; cwd?: string; instruction?: string }
export interface TeamDefinition {
  id?: string; name: string; description?: string; entry: string; members: TeamMember[];
  relations: Array<{ kind: 'delegate'; from: string[]; to: string }>;
  budget?: { maxDeliveries?: number }; createdAt?: string; updatedAt?: string;
}
export interface Coordination {
  autoHandover?: boolean;
  team?: { taskId: string; status: TeamStatus; entry: string; snapshot: { name: string }; error?: string };
  rootId: string; paused: boolean; directories: string[]; agents: string[];
  /** By instance id. Absent from older daemons. */
  defaults?: Record<string, MemberDefault>;
  /** How much members may do without asking. Absent from older daemons. */
  permission?: string;
  /** `host`: the member runs on another machine, and its thread is that machine's. */
  members: Array<{ id: string; name: string; host?: string; hostLabel?: string; thread: Thread | null; latest: CoordinationMessage | null }>;
  messages: CoordinationMessage[];
}
export interface CoordinationMessage {
  id: string; from: string; to: string; text: string; kind: string; state: string; result: string; createdAt: string; turnId: string | null;
  /** For a result: the message it answers. Absent from older daemons. */
  replyTo?: string | null;
  /** For a result from another machine: the file the member left there, kept by the hub. Absent from older daemons. */
  resultFile?: { name: string; size: number; truncated?: boolean };
}
export interface HandoverTarget { agent: string; model?: string }
/** How another account of the same agent takes over: a copy of the conversation, or a brief. Absent on older daemons. */
export type HandoverMethod = 'resume' | 'summary';
export interface HandoverSettings { enabled: boolean; threshold: number; orders: Record<string, HandoverTarget[]>; resume?: boolean; method?: HandoverMethod }
/** Quota-aware context compression ahead of the handover: another agent (a third-party bridge,
 *  say) reads the hub's transcript and writes the brief, so official-account quota buys no summarizing.
 *  `agents` are asked in order, the next when one is unavailable or fails; none means a native
 *  /compact in place. Absent on older daemons. */
export interface AutoCompactSettings { enabled: boolean; threshold: number; agents: HandoverTarget[]; minChars: number; chunkChars: number; maxChars: number }
export interface AutoCompactStatus { last?: { threadId: string; successorId?: string; native: boolean; chars: number; chunks: number; at: string }; error?: { threadId: string; message: string; at: string }; skipped?: { threadId: string; reason: string; chars: number; at: string } }
/** A watch waiting for the thread's agent quota to reset; the daemon then resends the failed turn. */
export interface ResumeView { origin: 'user' | 'global'; resetsAt: string | null; nextCheckAt: string; lastError?: string }
/** A transient failure the daemon is retrying by itself (`turn.retry` marks each attempt). */
export interface AutoRetryView { attempts: number; max: number; nextAt: string }
export interface AutoHandoverView {
  enabled: boolean; source: 'thread' | 'collaboration' | 'global'; override: boolean | null;
  state?: 'checking' | 'preparing' | 'countdown' | 'stopped' | 'failed';
  target?: HandoverTarget; deadline?: string; reason?: 'near' | 'exhausted'; error?: string;
  /** Where a preparing handover is; all but `summary` are past the point of calling it off. */
  step?: 'summary' | 'switch' | 'copy' | 'handover';
  /** Present when this thread waits for its quota to come back; absent on older daemons. */
  resume?: ResumeView | null;
}
/** The facts behind a thread that wants the person (`GET /api/threads/:id/brief`); sentences are the client's to write. */
export interface ThreadBrief {
  /** The thread's `updatedAt` when this was read. */
  updatedAt: string;
  kind: 'approval' | 'failure' | 'result' | null;
  approval?: { action: 'run' | 'edit' | 'delete' | 'other'; command: string | null; paths: string[]; moreFiles: number; title: string | null; pending: number };
  failure?: { code: string | null; edits: number; files: number; repeats: number; resume: boolean };
  result?: { first: string | null; edits: number; files: number };
}

export interface Thread {
  autoHandover?: AutoHandoverView;
  /** Present when the daemon retries this thread's transient failure; absent on older daemons. */
  autoRetry?: AutoRetryView | null;
  /** `doing`, on a member: its latest task while it works, the opening of its answer once it has returned. */
  coordination?: { retired?: boolean; rootId: string; children: number; paused: boolean; doing?: string; team?: { name: string; status: TeamStatus; role: string; error?: string } };
  id: string;
  agent: string;
  cwd: string;
  title: string;
  agentSessionId: string | null;
  status: 'idle' | 'running' | 'error';
  pinned: boolean;
  workMode?: WorkMode;
  /** Writing mode and its stage; null when off. Missing from older daemons. */
  writing?: WritingState | null;
  speed?: string | null;
  permissionMode: string;
  model: string | null;
  effort: string | null;
  archivedAt: string | null;
  sectionId: string | null;
  /** The thread this one was forked from, if it is a fork. */
  forkedFrom: string | null;
  handover?: { sourceId: string; title: string; agent: string; mode?: 'continue' | 'branch'; automatic?: boolean; detached?: boolean; method?: 'resume' | 'summary'; sourceSeq?: number; sourceMoved?: boolean } | null;
  /**
   * The git worktree the thread runs in, when a fork was given one. `of` is the directory it would
   * be in otherwise, which is the project it is listed under; `ofReal` is that with symlinks
   * resolved. Missing from daemons that predate worktrees.
   */
  worktree?: { root: string; branch: string; of: string; ofReal: string } | null;
  /** In no project: working in the hub's scratch area. Missing from daemons that predate it. */
  scratch?: boolean;
  /** The project directory no longer exists. Missing from daemons that predate it. */
  projectGone?: boolean;
  /** A turn finished and nobody has looked at the result yet. */
  unread: boolean;
  /** The newest thing the user asked, shown under the title. Null until the first ask. */
  preview: string | null;
  /** The scheduled task that started this thread, if any. Missing from older daemons. */
  schedule?: { id: string; name: string } | null;
  /** When the person marked the thread done on the board. Missing from older daemons. */
  doneAt?: string | null;
  /** The agent's latest progress line, kept while the board holds `progress.report`. */
  report?: ProgressReport | null;
  /** The terminal wrote to this session since the hub last did. */
  terminalActivity: boolean;
  /** The agent is waiting for the user to respond. */
  waiting: boolean;
  /** Missing from older daemons, which do not distinguish questions from approvals. */
  waitingReason?: 'approval' | 'question' | 'plan' | null;
  /** Messages sent while a turn was running; they go out as turns finish. */
  queued: QueuedMessage[];
  /** The upstream model-provider queue for the running turn. Missing from older daemons. */
  modelQueue?: ModelQueue | null;
  /** When the running turn started. Missing from older daemons, null while nothing runs. */
  runningSince?: string | null;
  /** Why the thread stopped, while its status is `error`. Missing from older daemons. */
  failure?: { code: string | null; message: string; at: string } | null;
  /** Shell command that continues this conversation in a terminal. Null before the first message. */
  resumeCommand: string | null;
  /** `cwd` with symlinks resolved, which is how agents report file paths. */
  cwdReal: string;
  createdAt: string;
  updatedAt: string;
  /**
   * Set only on rows this page builds for sessions that are not threads yet (never sent by the
   * daemon): the row mixes into the list by time, and clicking it imports the session.
   */
  external?: ExternalSession;
  /** Which Goal this conversation is on, for lists; absent on an ordinary one. */
  loopxInfo?: SessionLoopx | null;
  /** Which front end started the agent session (its app or its terminal client), when its file says. */
  origin?: 'app' | 'cli' | null;
}

/** A thread's native goal, as the goal card shows it. Served by the daemon, then updated by events. */
export interface GoalView {
  objective: string;
  status: string;
  tokensUsed?: number;
  tokenBudget?: number;
  /** When this goal was set. ISO. */
  startedAt: string;
  /** When this view was computed. ISO; while active the clock runs on from here. */
  at: string;
  /** Active work so far, ms. While active: elapsed = activeMs + (now − at). */
  activeMs: number;
}

/** What forking a thread can offer. The worktree is there for a thread in a git repository, with an agent that can move a copy. */
export interface ForkOptions {
  worktree: { available: boolean; reason?: string; branch?: string | null; changes?: number };
}

/** One firing of a scheduled task: how it went, and which thread to look at. */
export interface ScheduleRun {
  id: string;
  scheduleId: string;
  status: 'running' | 'done' | 'quiet' | 'error' | 'skipped' | 'missed';
  threadId: string | null;
  turnId: string | null;
  startedAt: string;
  finishedAt: string | null;
  message: string | null;
}

/** A recurring task: a prompt an agent runs in a directory on a cron schedule. */
export interface Schedule {
  id: string;
  name: string;
  agent: string;
  cwd: string;
  prompt: string;
  cron: string;
  mode: 'new' | 'same';
  threadId: string | null;
  permissionMode: string;
  enabled: boolean;
  timezone: string | null;
  catchUp: boolean;
  /** The cron in words. Empty for uncommon shapes: show the raw expression. */
  description: string;
  nextRunAt: string | null;
  lastRun: ScheduleRun | null;
  /** Why this cannot run as-is: a broken expression, a gone agent, a gone thread. */
  problem: string | null;
  createdAt: string;
  updatedAt: string;
}

/** A request the daemon refused. `details` is whatever it sent besides the message, for errors the page handles itself. */
/** A file the thread's agents changed, summed over every turn that touched it. */
export interface ChangedFile {
  path: string;
  added: number;
  removed: number;
  created: boolean;
  turns: number;
  turnId: string;
  at: string;
  /**
   * The diff references that touched it, in the order the log reported them, for the dialog to read
   * on demand. The daemon always sends it; a change it could not reference is an empty list.
   */
  details: Array<{ turnId: string; id: number; version: number }>;
  /** How many references there are, when `details` holds only the newest 20. */
  detailsTotal?: number;
  /** The last disk check found no ordinary file at this path. */
  missing?: true;
}
/** A picture or file an answer named, or the thread created; the last turn that did. */
export interface OutputFile {
  path: string;
  turnId: string;
  at: string;
  /** The last disk check found no ordinary file at this path. */
  missing?: true;
}
export interface ThreadOutputs {
  changes: { files: ChangedFile[]; added: number; removed: number; total: number };
  images: OutputFile[];
  files: OutputFile[];
  seq: number;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly status = 0,
  ) {
    const missingEndpoint = status === 404 && (message === 'not found' || message === 'HTTP 404');
    super(missingEndpoint ? t('api.updateBackend') : message);
    if (missingEndpoint) this.name = '';
  }
}

/** How many messages may wait behind a running turn. Matches the daemon's limit. */
export const MAX_QUEUED = 5;

/** One arrival in a batch the Router took in a single turn. */
export type TurnPart = { kind: 'person'; text: string } | { kind: 'lark' | 'worker' | 'meeting-prep'; count: number };
/** A turn a channel started on the person's behalf (a batch of Lark signals), not words they wrote. */
export interface TurnChannel { kind: string; count: number; parts?: TurnPart[] }

export interface QueuedMessage {
  channel?: TurnChannel;
  targets?: string[];
  error?: string;
  id: string;
  text: string;
  /** How many pictures go with it. */
  images: number;
  videos?: number;
  /** Attached files that are not videos. */
  files?: number;
}

export interface ModelQueue {
  state: string;
  position: number | null;
  operation?: string | null;
  message?: string | null;
  updatedAt: string;
}

export interface VideoRef { id: string; name: string; mimeType: string; size: number }

/** A user-made group in the sidebar. */
export interface Section {
  id: string;
  name: string;
  position: number;
}

/** One entry on the plan page. `parentId` is a top-level todo or null: one level of nesting. */
export interface ExploreRecommendation {
  agent: { agent: string; model: string | null; options: Array<{ agent: string; model: string | null; label: string; why: string; recommended?: boolean }> };
  profile: { draft: string; why: string; replace: boolean };
  people: Array<{ id: string; name: string; role: 'boss' | 'pm' | 'other'; selected: boolean; why: string; evidence: string[] }>;
  chats: Array<{ id: string; name: string; selected: boolean; why: string; evidence: string[] }>;
  meetings: { on: boolean; why: string; examples: string[] };
  intervalMin: { value: 15 | 30 | 60 | 120; why: string };
  brief: { enabled: boolean; morning: string; evening: string; why: string };
  push: { enabled: boolean; available: boolean; why: string };
  proactive: { mode: 'off' | 'observe' | 'auto'; why: string };
}

export interface ExploreStep {
  source: string;
  state: 'pending' | 'running' | 'done' | 'skipped' | 'failed';
  /** One human conclusion, shown as-is by the interface. */
  line?: string;
  /** How much this source read, shown as-is by the interface. */
  read?: string;
  reason?: string;
}

export interface ExploreStatus {
  id: string;
  state: 'collecting' | 'judging' | 'ready' | 'failed' | 'cancelled';
  steps: ExploreStep[];
  recommendation?: ExploreRecommendation;
  model?: 'ok' | 'fallback';
  error?: string;
  startedAt: string;
  finishedAt?: string;
}

export type ExploreSourceId = 'lark-profile' | 'lark-chats' | 'lark-messages' | 'lark-calendar' | 'loochat-threads';
export interface ExploreSource { id: ExploreSourceId; available: boolean; reason?: string }

export interface PersonalSettings { routerThreadId?: string | null; directoryIsDefault?: boolean; defaultDirectory?: string; defaultDirectoryDisplay?: string; directoryDisplay?: string | null; directory: string | null; agent: string | null; model: string | null }
export interface PersonalBrief { title: string; lines: string[]; text: string }
export type PlanRange = 'hour' | 'day' | 'week' | 'month';
export interface PlanMeeting { id: string; title: string; start: string; end: string; recurring: boolean; url?: string }
export interface PlanTodo { id: string; text: string; dueAt: string | null; importance: string | null; horizon: 'week' | 'month' | null; parentId: string | null; stage: TodoStage | null }
export interface PlanThread { id: string; title: string; line: string; at: string }
/** What lies ahead in one range, put together by the hub without a model. */
export interface PersonalPlan {
  range: PlanRange;
  from: string;
  to: string;
  /** `off`: no calendar to read; `unavailable`: reading failed. Neither is the same as no meetings. */
  meetings: { state: 'ok' | 'unavailable' | 'off'; error?: string; items: PlanMeeting[] };
  urgent: PlanTodo[];
  due: PlanTodo[];
  goals: PlanTodo[];
  needsYou: PlanThread[];
  running: PlanThread[];
}
export interface LastPersonalBrief { text: string; sentAt: string }
export interface BriefSettings { enabled: boolean; morning: string; evening: string }
export interface LarkChannelSettings {
  enabled: boolean; people: Array<{ id: string; name: string; role?: string }>; chats: Array<{ id: string; name: string }>;
  intervalMin: number; workHours?: { start: number; end: number; timeZone: string };
  /** Missing on older hubs: the configured event list is unknown. */
  events?: string[];
}
export interface LarkChannelStatus { cli: boolean; realtime?: LarkRealtime; topicErrors?: Array<{ ref: string; error: string }>; loggedIn: boolean; lastRunAt: string | null; nextRunAt: string | null; lastError: string | null; lastSignals: number }
export type LarkRealtime = 'off' | 'connecting' | 'live' | 'busy-elsewhere' | 'console-off' | 'unavailable' | 'error';
export interface SignalSource {
  type: string;
  ref: string;
  url?: string;
  from?: string;
  at?: string;
  thread?: { kind: string; ref: string };
}
export interface SignalMeeting {
  event: 'ended' | 'note-generated' | 'minutes-generated';
  meetingId?: string;
  calendarEventId?: string;
  noteId?: string;
  noteDocToken?: string;
  minuteToken?: string;
  title?: string;
  startedAt?: string;
  endedAt?: string;
}
/** Metadata only; material bodies are read through a separate endpoint. */
export interface SignalMaterial {
  state: 'unread' | 'not-ready' | 'ready' | 'empty' | 'failed';
  kind?: 'note' | 'minutes';
  checkedAt?: string;
  error?: string;
  partial?: boolean;
}
export interface SignalMaterialRead {
  key: string;
  material: SignalMaterial;
  content: string | null;
}
/** A signal the channel kept on a matter, and where it went. */
export interface MatterSignal {
  key: string;
  at: string;
  summary: string | null;
  disposition: { outcome: 'filtered' | 'delivered' | 'router'; reason?: string; pending?: boolean };
  source?: SignalSource | null;
  meeting?: SignalMeeting | null;
  material?: SignalMaterial | null;
}
export interface LarkSearchResult { id: string; name: string; hint?: string }
export interface TodoNote { text: string; updatedAt: string | null; threadId: string | null; rev: number }
export interface PetStatus { id: string; commit: string; installed: boolean; installing: boolean; error: string | null; clips: Record<string, number>; frameMs: number }
export interface TodoSummaryStatus { threadId: string | null; running: boolean; days: number | null; startedAt: string | null }
export interface Wish { id: string; threadId: string; text: string; createdAt: string }
export interface PushSettings { enabled: boolean; recipient: string; baseUrl: string; waiting: boolean; failure: boolean; notice: boolean; scheduled: boolean }
export interface PushRecord { key: string; at: string; kind: string; title: string; result: 'queued' | 'sent' | 'failed' | 'dropped'; reason: string | null }
export interface PushStatus { cli: boolean; botReady: boolean; recipient: string; reason: string | null; records: PushRecord[]; dropped: number }
export interface FeishuSettings { enabled: boolean; agent: string; cwd: string; owner: string; echoWeb: boolean }
export type FeishuRealtime = 'off' | 'connecting' | 'live' | 'console-off' | 'busy-elsewhere' | 'unavailable' | 'error';
export interface FeishuRecord { key: string; at: string; direction: 'in' | 'out'; threadId: string | null; title: string; result: 'received' | 'sent' | 'failed' | 'dropped'; reason: string | null }
export interface FeishuSetup { state: 'idle' | 'waiting' | 'done' | 'failed'; /** What the pairing is for: creating the bot, or confirming the owner's identity. */ kind: 'create' | 'login' | null; url: string | null; reason: string | null }
export interface FeishuStatus {
  /** Whether lark-cli is on the machine at all. */
  cli: boolean;
  /** Whether the dedicated bot (profile) exists; until then the page only offers to create it. */
  configured: boolean;
  botReady: boolean;
  realtime: FeishuRealtime;
  /** Why the channel is not ready, when it is not: `cli_missing` or `bot_not_ready`. */
  reason: string | null;
  /** The open_id of whoever messaged the bot while no owner was bound: offered as the owner to confirm. */
  pendingOwner: string | null;
  setup: FeishuSetup;
  /** The latest only, unless the full status is asked for. */
  records: FeishuRecord[];
  dropped: number;
}
export interface TodoSource { type: 'lark-task' | 'lark-message' | 'lark-flag' | 'lark-calendar' | 'manual'; ref: string; url?: string; from?: string; at?: string }
export interface TodoUrgency { urgent: boolean; reason: 'overdue' | 'due-soon' | 'pinned' | 'meeting-today' | null }
/** Whether the assistant may act on its own, and in which repositories she may work in a sandbox. */
export type ProactiveMode = 'off' | 'observe' | 'auto';
export interface ProactiveSettings {
  /** off: nothing; observe: only note what she would have done; auto: really do it. */
  mode: ProactiveMode;
  repos: string[];
  /** Read-only, for older clients: `mode !== 'off'`. */
  enabled: boolean;
}

export interface ProactiveFile {
  path: string;
  additions: number;
  deletions: number;
}

export type ProactiveStatus = 'running' | 'observed' | 'approved' | 'ready' | 'merged' | 'discarded' | 'reverted' | 'failed' | 'waiting' | 'asking' | 'answered' | 'continued' | 'closed';

/** One thing she did unasked: materials prepared for a matter, or code changed in a sandbox. */
export interface ProactiveRun {
  id: string;
  /** Empty for an advance on a session that belongs to no matter. */
  matterId: string;
  /** The session's title, for a card that has no matter to name it. */
  threadTitle?: string;
  kind: 'prepare' | 'sandbox' | 'advance' | 'dispatch';
  /** Dispatch only: the agent, directory and task proposed, as the card shows them. */
  agent?: string;
  cwd?: string;
  task?: string;
  /** Dispatch only: defaults to the task's first line, for a card with no matter to name it. */
  title?: string;
  model?: string;
  variant?: 'deadline' | 'plan';
  needsRepo?: true;
  turnId?: string;
  phase?: 'done' | 'blocked' | 'unsure';
  on?: 'user' | 'external';
  line?: string;
  question?: { text: string; options: string[] };
  wait?: { what: string; then: string };
  wakeAt?: string;
  attempts?: number;
  reason?: 'transient' | 'external' | 'user' | 'done';
  repo?: string;
  branch?: string;
  threadId: string;
  startedAt: string;
  endedAt?: string;
  status: ProactiveStatus;
  /** What was done, or why it failed. */
  summary?: string;
  /** The worker's own account of the tests. */
  testReport?: string;
  files?: ProactiveFile[];
  /** Why she dropped the work herself, for an audit list. */
  discardReason?: string;
}

export type ProactiveAction = 'merge' | 'discard' | 'revert' | 'approve' | 'answer' | 'close-matter' | 'dismiss' | 'allow-repo' | 'start';

/** Where a matter stands, derived by the daemon. Null when nothing is going on with it. */
export type TodoStage = 'answer' | 'review' | 'blocked' | 'working' | 'idle';
export interface TodoSubscription { kind: 'lark-thread' | 'lark-task' | 'lark-calendar'; ref: string; at: string }

export interface Todo {
  owner?: 'me' | 'agent';
  state?: 'open' | 'waiting' | 'done';
  lane?: 'you' | 'doing' | 'wait' | 'later' | null;
  importance?: 'high' | 'normal' | 'low' | null;
  urgentUntil?: string | null;
  source?: TodoSource | null;
  /** Daemon-derived, never stored; only present on list responses. */
  urgency?: TodoUrgency;
  waitingOn?: string | null;
  dueAt?: string | null;
  followUpAt?: string | null;
  followUpPaused?: boolean;
  hasNote?: boolean;
  noteUpdatedAt?: string | null;
  /** First line of the continuation note's next steps; the desk cover falls back to thread titles. */
  notePreview?: string | null;
  /** How many things the assistant did on its own on this matter; the runs themselves load when asked for. */
  proactiveRunCount?: number;
  stage?: TodoStage | null;
  /** The outside threads this matter follows. */
  subscriptions?: TodoSubscription[];
  proposed?: boolean;
  rev?: number;
  proposals?: Array<{ id: string; kind: 'add' | 'done'; evidence: string; threadId: string }>;
  id: string;
  text: string;
  done: boolean;
  parentId: string | null;
  position: number;
  createdAt: string;
  /** Last time anyone did anything to this plan; the desk's last-activity signal. */
  updatedAt?: string;
  doneAt: string | null;
  /** Threads linked to this plan, as full views so chips can show agent, title and state. */
  threads: Thread[];
}

/** An image ready to be sent with a message. */
export interface OutgoingImage {
  data: string;
  mimeType: string;
}

export interface DirectoryEntry {
  name: string;
  type: 'file' | 'dir';
  size?: number;
  modified?: string;
}

export interface DirectoryInfo {
  path: string;
  files: DirectoryEntry[];
  parent?: string | null;
}

export interface ProjectDirectoryInfo {
  path: string;
  /** Where the breadcrumb trail starts; absent on older daemons. */
  home?: string;
  parent: string | null;
  directories: Array<{ name: string; path: string }>;
  allowed: boolean;
  selectable: boolean;
}

export interface DirectoryTreeNode {
  name: string;
  path: string;
  type: 'dir';
  children?: DirectoryTreeNode[];
}

export interface SlashCommand {
  name: string;
  description: string;
  /** What to type after the command, as the agent describes it. */
  hint?: string;
  /** Present only when the agent says what the entry is. */
  kind?: 'command' | 'prompt' | 'skill';
}

export interface ModelOption {
  id: string;
  label: string;
  /** Short note from the agent, such as the concrete version behind an alias. */
  description?: string;
  /** How busy this model is right now; above 100 means overloaded. */
  loadPercent?: number;
  /** Total queued requests. Null means unavailable, not zero; absent means no queue metadata. */
  queueSize?: number | null;
  /** Observed request position, not total queue size. Zero means observed nonqueued; null means unknown/stale. */
  queuePosition?: number | null;
  /** Queue observation time in epoch milliseconds; absent when no valid observation exists. */
  queueObservedAt?: number;
  /** True when reading the upstream load failed, rather than the upstream omitting it. */
  loadError?: boolean;
  /** Effort levels this model accepts. Empty: none. Missing: use the agent-wide list. */
  efforts?: string[];
  speeds?: Array<{ id: string; label: string; description?: string; isFast?: boolean }>;
  /** False when the model cannot read images. */
  images?: boolean;
  /** How images reach a model that cannot read them, such as a tool that hands them to a vision model. */
  imagesVia?: string;
  /** The model the agent uses when the thread does not pick one. */
  isDefault?: boolean;
}

/** Older Hub responses lack isFast. Only recognize their advertised public fast-tier IDs. */
export function isFastSpeed(speed: NonNullable<ModelOption['speeds']>[number]): boolean {
  return speed.isFast ?? ['priority', 'fast', 'ultrafast'].includes(speed.id);
}

/** What would break if an agent instance disappeared from the registry. */
export interface AgentReferences {
  threads: number;
  schedules: number;
  teams: number;
  handoverOrders: number;
}

/** An opt-in bridge that changes only a private runtime copy; incompatible installs fall back. */
export interface StreamBridgeCapability {
  platform: string;
  temporaryMegabytes: number;
}

export interface AgentInfo {
  id: string;
  label: string;
  hidden?: boolean;
  /** The agent's mark: the path of a 24×24 drawing, filled with the even-odd rule. Missing from daemons that predate it. */
  icon?: string;
  /** Which adapter this instance is; instances of one adapter group together in the picker. */
  adapter?: string;
  /** The login this instance runs as, when it is a linked account (an email). */
  account?: string | null;
  available: boolean;
  unavailableReason?: string;
  /** How it can be signed in from the hub, when its adapter can start the CLI's login. */
  login?: 'terminal' | 'wizard';
  /** In-progress wizard login that can be resumed after navigation. */
  activeLogin?: AgentLogin;
  streamBridgeEnabled?: boolean;
  capabilities: {
    workModes?: WorkMode[];
    speed?: boolean;
    coordination?: boolean;
    resume: boolean;
    approvals: boolean;
    memory?: boolean;
    permissionModes: string[];
    /** The level a new thread starts at. Missing: `auto_edit`. */
    defaultPermissionMode?: string;
    /** False when native model discovery is unavailable; no model picker is offered. */
    modelCatalog?: boolean;
    streamBridge?: StreamBridgeCapability;
    effortLevels: string[];
    images: boolean;
    deleteSession: boolean;
    /** Can list its past sessions and replay them, so they can be imported. */
    history: boolean;
    /** Can read plan usage outside a session, for the quota panel. */
    quota: boolean;
    compact?: boolean;
    /** Native realtime two-way voice capability. */
    realtimeVoice?: boolean;
    goal?: { controls: Array<'show' | 'pause' | 'clear'>; actions?: Array<'set' | 'show' | 'pause' | 'resume' | 'clear'>; /** The hub follows the goal's state live, so a card replaces the menu commands. */
      live?: boolean };
    fork: boolean;
    /** A fork can end at an earlier turn instead of taking the whole thread. Missing from daemons that predate it. */
    forkAt?: boolean;
    /** An earlier user message can be edited and sent again from there. Missing from older daemons. */
    editMessage?: boolean;
    /** A fork can run in a git worktree of its own. Missing from daemons that predate it. */
    forkInto?: boolean;
    /** The whole prompt of a conversation can be read from the agent's session file. Missing from daemons that predate it. */
    systemPrompt?: boolean;
  };
}

export interface AgentLogin {
  id: string;
  status: 'starting' | 'waiting' | 'submitting' | 'success' | 'error' | 'cancelled';
  url?: string;
  input?: 'code-or-url' | 'none';
  userCode?: string;
  error?: 'failed' | 'expired' | 'unavailable' | 'verification';
}

export interface SupportedAgent {
  adapter: string;
  label: string;
  icon?: string;
  installGuide?: string;
  installUrl?: string;
  capabilities?: { createAccount: boolean; streamBridge?: StreamBridgeCapability };
}

/** One window of a plan's usage, as the agent itself reports it. */
export interface QuotaWindow {
  /** Short chip label, such as "5H" or "7D". */
  label: string;
  /** What the window covers, when the agent splits by model group. */
  scope?: string;
  /** How much of the window is used, 0 to 100. */
  usedPercent: number;
  /** When the window resets, if the agent said so. ISO. */
  resetsAt?: string;
  /** How busy the model behind this window is right now; above 100 means overloaded. */
  loadPercent?: number;
}

/** Money left on a pay-as-you-go key, as the vendor states it, such as "¥60.52". */
export interface QuotaBalance {
  scope: string;
  amount: string;
}

/** Plan usage of an agent instance, read outside any session. */
export interface PlanQuota {
  windows: QuotaWindow[];
  /** Balances of keys billed per use, next to the plans' windows. */
  balances?: QuotaBalance[];
  note?: string;
  /** The subscription the account is on, as the agent reports it (`id`) and as shown (`label`). */
  plan?: { id: string; label: string };
  /** When the agent was last asked. ISO. */
  checkedAt: string;
}

/** A conversation the agent already has, as offered for import. */
export interface AgentSession {
  id: string;
  cwd: string;
  title: string;
  updatedAt: string | null;
  /** Set when this session is already a thread here (possibly an archived one). */
  threadId: string | null;
}

/** A conversation an agent on this machine already has, that is not a thread here yet. It mixes
 *  into the thread list by its time; opening it imports it and continues it as a thread. */
export interface ExternalSession {
  /** The agent instance id, as threads store it. */
  agent: string;
  sessionId: string;
  cwd: string;
  title: string;
  updatedAt: string | null;
  /** Which front end started it (the agent's app or its terminal client), when its file says. */
  origin?: 'app' | 'cli' | null;
  loopx?: SessionLoopx;
}

/** What makes a conversation a LoopX Goal conversation, and how that was found out. */
export interface SessionLoopx {
  goalId: string;
  /** The Goal's name; the id itself when the core gave none. */
  goalTitle: string;
  /** The core no longer has this Goal: the conversation is listed, bound to nothing. */
  missing?: boolean;
  /** `core`: the core's external-session bindings; `content`: the session's own text; `workbench`: started here. */
  basis: 'core' | 'content' | 'workbench';
  source: string;
  goals?: string[];
}

/** One page of `GET /api/local-sessions`. */
export interface LocalSessionPage {
  sessions: ExternalSession[];
  nextCursor: string | null;
  scannedAt: string | null;
}

/** The four kinds of tokens and the replies, summed over some time. */
export interface UsageTokens {
  input: number;
  cacheWrite: number;
  cacheRead: number;
  output: number;
  replies: number;
}

/** One step of the machine usage series. A step with no usage while the daemon was not running
 *  is a gap, drawn as a break rather than a zero. */
export interface UsagePoint extends UsageTokens {
  /** The step's start, epoch seconds. */
  t: number;
  gap: boolean;
}

/** `GET /api/machine-usage`. */
export interface MachineUsageReport {
  hours: 1 | 24 | 168;
  /** Seconds per point: 60 within the hour, 600 over the day, 3600 over the week. */
  step: 60 | 600 | 3600;
  total: UsageTokens;
  points: UsagePoint[];
  /** Totals per model and per launcher, busiest first, from the unfiltered window. */
  models: MachineUsageShare[];
  /** The empty name is the "other" launcher: a session the hub did not start. */
  launchers: MachineUsageShare[];
}

/** Totals for one model or launcher over the window. */
export interface MachineUsageShare extends UsageTokens {
  name: string;
}

/** A web terminal, as the daemon lists it. Exited ones are not listed. */
export interface TerminalInfo {
  id: string;
  cwd: string;
  cols: number;
  rows: number;
  startedAt: number;
  lastActivity: number;
}

/** One SSE frame of a terminal stream. Output is base64: programs may emit non-UTF-8 bytes. */
export type TerminalFrame = { type: 'backlog'; data: string } | { type: 'out'; data: string } | { type: 'exit'; code: number };

/** Connection and operation errors from another machine. */
export type HostFailure = 'not_joined' | 'relay_lost' | 'unreachable' | 'host_offline' | 'too_large';

/** A folder an agent reads skills from. `agents` are the instances that see it. */
export interface SkillLocation {
  path: string;
  scope: 'personal' | 'shared';
  agents: string[];
}

/** One skill as it stands in one folder. */
export interface SkillPlace {
  /** The folder the skill sits in, not the skill's own folder. */
  path: string;
  mtime: string;
  sizeBytes: number;
  fileCount: number;
  /** sha256 of the layout plus the SKILL.md: how two machines tell they hold the same. */
  digest: string;
  agents: string[];
}

/** A skill merged across the folders it sits in, newest first. */
export interface SkillEntry extends SkillPlace {
  name: string;
  description: string;
  locations: SkillPlace[];
  /** Union of the agents that see it somewhere. */
  agents: string[];
}

export interface SkillsListing {
  locations: SkillLocation[];
  skills: SkillEntry[];
}

export interface SkillDetail extends SkillEntry {
  skillMd: string;
}

/** A skill packed to travel between machines. */
export interface SkillPayload {
  name: string;
  description?: string;
  files: Array<{ path: string; mode?: number; data: string }>;
  sizeBytes: number;
  digest: string;
}

export type SkillInstallStatus = 'installed' | 'exists' | 'same' | 'no_dir' | 'error';

export interface SkillInstallOutcome {
  agent: string;
  dir: string;
  status: SkillInstallStatus;
  error?: string;
}

/** One MCP server as one agent instance's own config states it. `config` is the server object
 * verbatim, so it can be shown, copied and reinstalled without knowing every agent's extras. */
export interface McpServer {
  name: string;
  config: Record<string, unknown>;
  sourcePath: string;
}

/** One agent instance's MCP servers, as the machine this client is for reads them. */
export interface McpAgentListing {
  agent: string;
  writable: boolean;
  servers: McpServer[];
  error?: string;
}

export interface McpListing {
  agents: McpAgentListing[];
}

export type McpInstallStatus = 'installed' | 'exists' | 'same' | 'error';

export interface McpInstallOutcome {
  agent: string;
  status: McpInstallStatus;
  error?: string;
}

/** The hub's own note about one server name: the business line and the purpose in words, plus the
 * config for servers registered on the page rather than found in any config file. */
export interface McpEntry {
  name: string;
  businessLine: string;
  description: string;
  server: Record<string, unknown> | null;
  createdAt: string;
  updatedAt: string;
}

/** Another machine this hub shows: a daemon connected through its relay client. */
export interface HostInfo {
  id: string;
  label: string;
  /** The name reported by the node. */
  target: string;
  status: 'connecting' | 'online' | 'offline';
  code?: HostFailure;
  /** The connection failure reported by the hub. */
  error?: string;
  version?: string;
}

/** Where a path of the API is when another machine answers it: the same path, behind the hub's proxy. */
export function hostPath(host: string, path: string): string {
  return path.startsWith('/api/') ? `/api/hosts/${encodeURIComponent(host)}/proxy/${path.slice('/api/'.length)}` : path;
}

const TOKEN_KEY = 'calli.token';

/** Token lives in localStorage on this device only. Storage can be blocked, so every access is guarded. */
export function loadToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}

export function saveToken(token: string): void {
  if (!token || token !== loadToken()) { clearQuotaCache(); clearThreadCache(); clearHostCache(); }
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage unavailable: the token is kept in memory for this page only.
  }
}

/** The hub turned the credential down. `code` says which way a login link died, when it did. */
export class AuthError extends Error {
  constructor(message: string, readonly code?: 'link_expired' | 'link_revoked') {
    super(message);
  }
}

/** A 401 as an AuthError, carrying how a login link died when that is what happened. */
async function authError(res: Response): Promise<AuthError> {
  const body = await res.json().catch(() => ({})) as { auth_error?: string };
  const code = body.auth_error === 'link_expired' || body.auth_error === 'link_revoked' ? body.auth_error : undefined;
  return new AuthError('unauthorized', code);
}

export interface VoiceProvider {
  login?: 'ready' | 'required';
  id: string;
  available: boolean;
  supported: boolean;
  reason?: string;
}

export interface VoiceSnapshot {
  id: string;
  hostId: string;
  providerId: string;
  threadId: string;
  state: 'connecting' | 'connected' | 'stopping' | 'closed' | 'failed';
  startAccepted: boolean;
  upstreamStarted: boolean;
  mediaConnected: boolean;
  reason?: 'ended' | 'expired' | 'startup_timeout' | 'revoked' | 'provider_error' | 'provider_closed' | 'shutdown';
}

export type VoiceEvent =
  | { type: 'state'; session: VoiceSnapshot }
  | { type: 'sdp'; sdp: string }
  | { type: 'transcript'; itemId: string; text: string; final: boolean;
      role?: 'user' | 'assistant'; at?: string; afterSeq?: number; order?: number };

/**
 * Random UUID per browser tab session, sent in X-LooChat-Voice-Owner header.
 * Generated in memory; never saved to storage and never exposed to extensions.
 */
const TAB_VOICE_OWNER =
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `tab-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

export function getTabVoiceOwner(): string {
  return TAB_VOICE_OWNER;
}

/** The server pings every 15 seconds; a stream silent for this long is dead, not quiet. */
const STREAM_IDLE_MS = 40_000;
/** Back in the foreground, a stream that has said nothing for this long is reconnected at once. */
const STREAM_WAKE_MS = 5_000;

export class Api {
  private readonly videoUploads = new WeakMap<File, Map<string, Promise<VideoRef>>>();
  private readonly quotas = new Map<string, PlanQuota>();
  private readonly quotaRequests = new Map<string, Promise<PlanQuota>>();

  /** True for the made-up workspace shown before the page is connected: nothing reaches a daemon. */
  readonly demo: boolean = false;

  constructor(
    private readonly token: string,
    private readonly base = '',
    /** Another machine's id: every call is then answered by that machine's daemon, through the hub. */
    readonly host: string | null = null,
  ) {}

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    // The language picked in the hub, not the browser's: what the daemon writes for people can follow it.
    return { Authorization: `Bearer ${this.token}`, 'Accept-Language': localeTag(), ...extra };
  }

  private url(path: string): string {
    return this.base + (this.host ? hostPath(this.host, path) : path);
  }

  private request<T>(method: string, path: string, body?: unknown, opts: { keepalive?: boolean; signal?: AbortSignal } = {}): Promise<T> {
    return this.send(method, this.url(path), body, opts);
  }

  protected async send<T>(method: string, url: string, body?: unknown, opts: { keepalive?: boolean; signal?: AbortSignal } = {}): Promise<T> {
    const res = await fetch(url, {
      method,
      headers: this.headers(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      body: body === undefined ? undefined : JSON.stringify(body),
      keepalive: opts.keepalive,
      signal: opts.signal,
    });
    if (res.status === 401) {
      const body = await res.json().catch(() => ({})) as { auth_error?: string };
      const code = body.auth_error === 'link_expired' || body.auth_error === 'link_revoked' ? body.auth_error : undefined;
      throw new AuthError('unauthorized', code);
    }
    if (res.status === 204) return undefined as T;
    const json = await res.json().catch(error => {
      // A body interrupted by navigation is a failed read, not a successful empty record.
      if (res.ok) throw error;
      return {};
    });
    if (!res.ok) throw new ApiError((json as { error?: string }).error ?? `HTTP ${res.status}`, json as Record<string, unknown>, res.status);
    return json as T;
  }

  // The machines themselves are the hub's own, whichever machine this client is for.

  hosts(): Promise<HostInfo[]> {
    return this.send('GET', `${this.base}/api/hosts`);
  }

  /** Adds a machine that will connect out to the hub; the answer's token is shown this once. */
  addHost(label: string): Promise<HostInfo & { token: string }> {
    return this.send('POST', `${this.base}/api/hosts`, { label });
  }

  removeHost(id: string): Promise<void> {
    return this.send('DELETE', `${this.base}/api/hosts/${encodeURIComponent(id)}`);
  }

  /** Refreshes the connection state; the node reconnects automatically. */
  reconnectHost(id: string): Promise<HostInfo> {
    return this.send('POST', `${this.base}/api/hosts/${encodeURIComponent(id)}/reconnect`);
  }

  health(): Promise<{ ok: boolean; version: string }> {
    return this.request('GET', '/api/health');
  }

  agents(): Promise<AgentInfo[]> {
    return this.request('GET', '/api/agents');
  }

  workdirs(): Promise<WorkdirsConfig> {
    return this.request('GET', '/api/config');
  }

  /** Allows another directory as a project root, from the page. */
  addWorkdir(path: string): Promise<WorkdirsConfig> {
    return this.request('POST', '/api/workdirs', { path });
  }

  /** Takes back a root that was added from the page. */
  removeWorkdir(path: string): Promise<WorkdirsConfig> {
    return this.request('DELETE', `/api/workdirs?path=${encodeURIComponent(path)}`);
  }

  /** A new project folder in `parent`, a git repository with a first commit when `git` is set. */
  createProject(input: { parent: string; name: string; git: boolean }): Promise<{ path: string; git: boolean }> {
    return this.request('POST', '/api/projects', input);
  }

  threads(opts: { archived?: boolean } = {}): Promise<Thread[]> {
    return this.request('GET', `/api/threads${opts.archived ? '?archived=1' : ''}`);
  }

  sections(): Promise<Section[]> {
    return this.request('GET', '/api/sections');
  }

  createSection(name: string): Promise<Section> {
    return this.request('POST', '/api/sections', { name });
  }

  renameSection(id: string, name: string): Promise<Section> {
    return this.request('PATCH', `/api/sections/${id}`, { name });
  }

  deleteSection(id: string): Promise<void> {
    return this.request('DELETE', `/api/sections/${id}`);
  }

  generalSettings(): Promise<GeneralSettings> { return this.request('GET', '/api/general-settings'); }
  saveGeneralSettings(value: GeneralSettings): Promise<GeneralSettings> { return this.request('PUT', '/api/general-settings', value); }
  exploreSources(): Promise<ExploreSource[]> { return this.request('GET', '/api/personal/explore/sources'); }
  startExplore(sources: ExploreSourceId[]): Promise<{ id: string }> { return this.request('POST', '/api/personal/explore', { sources }); }
  exploreStatus(): Promise<ExploreStatus | null> { return this.request('GET', '/api/personal/explore'); }
  cancelExplore(): Promise<{ cancelled: boolean }> { return this.request('DELETE', '/api/personal/explore'); }
  draftModelPreferences(): Promise<Record<string, DraftModelPreference>> { return this.request('GET', '/api/draft-model-preferences'); }
  patchDraftModelPreference(agent: string, patch: ModelPreferencePatch): Promise<DraftModelPreference> { return this.request('PATCH', '/api/draft-model-preferences', { agent, ...patch }, { keepalive: true }); }
  personalSettings(): Promise<PersonalSettings> { return this.request('GET', '/api/personal/settings'); }
  /** Switches her agent in place; a conversation under way is handed over to the new one. */
  switchPersonalAgent(agent: string): Promise<{ threadId: string | null; settings: PersonalSettings }> { return this.request('POST', '/api/personal/agent', { agent }); }
  savePersonalSettings(value: PersonalSettings): Promise<PersonalSettings> { return this.request('PUT', '/api/personal/settings', value); }
  personalPlan(range: PlanRange, signal?: AbortSignal): Promise<PersonalPlan> { return this.request('GET', `/api/personal/plan?range=${range}`, undefined, { signal }); }
  personalBrief(kind: 'morning' | 'evening'): Promise<PersonalBrief | null> { return this.request('GET', `/api/personal/brief?kind=${kind}`); }
  lastPersonalBrief(kind: 'morning' | 'evening'): Promise<LastPersonalBrief | null> { return this.request('GET', `/api/personal/brief/last?kind=${kind}`); }
  briefSettings(): Promise<BriefSettings> { return this.request('GET', '/api/personal/brief/settings'); }
  saveBriefSettings(value: BriefSettings): Promise<BriefSettings> { return this.request('PUT', '/api/personal/brief/settings', value); }
  proactive(matterId?: string): Promise<{ settings: ProactiveSettings; runs: ProactiveRun[] }> { return this.request('GET', `/api/personal/proactive${matterId ? `?matterId=${encodeURIComponent(matterId)}` : ''}`); }
  proactiveSettings(): Promise<{ settings: ProactiveSettings; counts: Record<string, number>; asking: Record<string, number> }> { return this.request('GET', '/api/personal/proactive?summary=true'); }
  saveProactiveSettings(value: Partial<Omit<ProactiveSettings, 'enabled'>>): Promise<ProactiveSettings> { return this.request('PUT', '/api/personal/proactive/settings', value); }
  proactiveDiff(id: string): Promise<{ files: ProactiveFile[] }> { return this.request('GET', `/api/personal/proactive/runs/${encodeURIComponent(id)}/diff`); }
  proactiveAct(id: string, action: ProactiveAction, answer?: string): Promise<ProactiveRun> { return this.request('POST', `/api/personal/proactive/runs/${encodeURIComponent(id)}/${action}`, action === 'answer' ? { answer } : undefined); }
  larkChannel(): Promise<LarkChannelSettings> { return this.request('GET', '/api/personal/channels/lark'); }
  saveLarkChannel(value: LarkChannelSettings): Promise<LarkChannelSettings> { return this.request('PUT', '/api/personal/channels/lark', value); }
  larkStatus(): Promise<LarkChannelStatus> { return this.request('GET', '/api/personal/channels/lark/status'); }
  runLark(): Promise<LarkChannelStatus> { return this.request('POST', '/api/personal/channels/lark/run'); }
  searchLark(kind: 'people' | 'chats', q: string): Promise<LarkSearchResult[]> { return this.request('GET', `/api/personal/channels/lark/search?kind=${kind}&q=${encodeURIComponent(q)}`); }
  matterSignals(matterId: string): Promise<{ signals: MatterSignal[] }> { return this.request('GET', `/api/personal/signals?matterId=${encodeURIComponent(matterId)}`); }
  readSignalMaterial(key: string): Promise<SignalMaterialRead> { return this.request('GET', `/api/personal/signals/${encodeURIComponent(key)}/material`); }
  todoNote(id: string): Promise<TodoNote> { return this.request('GET', `/api/todos/${id}/note`); }
  /** The desk's companion: whether its frames are here yet. Always this hub's own, never another machine's. */
  pet(): Promise<PetStatus> { return this.send('GET', `${this.base}/api/pets`); }
  installPet(): Promise<PetStatus> { return this.send('POST', `${this.base}/api/pets/install`); }
  petSay(text: string): Promise<{ threadId: string }> { return this.request('POST', '/api/personal/say', { text }); }
  petRouter(): Promise<{ threadId: string | null; status: 'idle' | 'running' | 'error' | null; lastReply: string | null; lastReplyAt: string | null }> {
    return this.request('GET', '/api/personal/router');
  }
  personalProfile(): Promise<{ text: string; updatedAt: string | null; path: string; isTemplate: boolean; rev: number }> { return this.request('GET', '/api/personal/profile'); }
  savePersonalProfile(text: string, rev?: number): Promise<{ text: string; updatedAt: string; path: string; isTemplate: boolean; rev: number }> {
    return this.request('PUT', '/api/personal/profile', rev === undefined ? { text } : { text, rev });
  }
  /** Frames are public artwork, loaded by `<img>` without a token. */
  petFrame(id: string, clip: string, index: number): string { return `${this.base}/pets/${id}/${clip}/${clip}_${String(index * 2 + 1).padStart(3, '0')}.webp`; }
  resolveTodoProposal(id: string, pid: string, action: 'accept' | 'dismiss', rev?: number): Promise<Todo | null> { return this.request('POST', `/api/todos/${id}/proposals/${pid}/${action}`, { rev }); }
  delegateTodo(id: string, cwd?: string): Promise<Thread> { return this.request('POST', `/api/todos/${id}/delegate`, { cwd }); }
  /** The latest "summarize recent days" run on the plan page. */
  todoSummary(): Promise<TodoSummaryStatus> { return this.request('GET', '/api/todos/summary'); }
  /** Starts a personal thread that summarizes recent threads into the plan; `heading` is the parent item's text. */
  summarizeTodos(days: number, title: string, heading: string): Promise<Thread> { return this.request('POST', '/api/todos/summary', { days, title, heading }); }
  wishCount(): Promise<{ count: number }> { return this.request('GET', '/api/wishes?summary=true'); }
  wishes(): Promise<Wish[]> { return this.request('GET', '/api/wishes'); }
  pushSettings(): Promise<PushSettings> { return this.request('GET', '/api/push/settings'); }
  savePushSettings(value: PushSettings): Promise<PushSettings> { return this.request('PUT', '/api/push/settings', value); }
  pushStatus(full = false): Promise<PushStatus> { return this.request('GET', `/api/push/status${full ? '' : '?summary=true'}`); }
  testPush(): Promise<PushRecord> { return this.request('POST', '/api/push/test', {}); }
  feishuSettings(): Promise<FeishuSettings> { return this.request('GET', '/api/feishu/settings'); }
  saveFeishuSettings(value: FeishuSettings): Promise<FeishuSettings> { return this.request('PUT', '/api/feishu/settings', value); }
  feishuStatus(full = false): Promise<FeishuStatus> { return this.request('GET', `/api/feishu/status${full ? '' : '?summary=true'}`); }
  /** Starts creating the dedicated bot; the status that returns carries the authorization URL to open in Feishu. */
  startFeishuSetup(): Promise<FeishuStatus> { return this.request('POST', '/api/feishu/setup', {}); }
  /** Starts the owner's device-flow sign-in; the scan lands, the daemon fills the owner with that open_id. */
  startFeishuLogin(): Promise<FeishuStatus> { return this.request('POST', '/api/feishu/login', {}); }
  cancelFeishuSetup(): Promise<FeishuStatus> { return this.request('DELETE', '/api/feishu/setup'); }
  /** Sends the owner one test message; the record that returns says how it went. */
  testFeishu(): Promise<FeishuRecord> { return this.request('POST', '/api/feishu/test', {}); }

  todos(): Promise<Todo[]> {
    return this.request('GET', '/api/todos');
  }

  createTodo(text: string, parentId?: string | null): Promise<Todo> {
    return this.request('POST', '/api/todos', { text, parentId });
  }

  updateTodo(id: string, patch: { text?: string; done?: boolean; lane?: 'you' | 'doing' | 'wait' | 'later' | null; importance?: 'high' | 'normal' | 'low' | null; urgentUntil?: string | null; source?: TodoSource | null; touch?: boolean; followUpAt?: string | null; unsubscribe?: Array<Pick<TodoSubscription, 'kind' | 'ref'>> }): Promise<Todo> {
    return this.request('PATCH', `/api/todos/${id}`, patch);
  }

  deleteTodo(id: string): Promise<void> {
    return this.request('DELETE', `/api/todos/${id}`);
  }

  /** Puts an item under `parentId` (null: top level), before the sibling `before` (null: last). */
  moveTodo(id: string, to: { parentId: string | null; before: string | null }): Promise<void> {
    return this.request('POST', `/api/todos/${id}/move`, to);
  }

  linkTodo(id: string, threadId: string): Promise<void> {
    return this.request('POST', `/api/todos/${id}/threads`, { threadId });
  }

  unlinkTodo(id: string, threadId: string): Promise<void> {
    return this.request('DELETE', `/api/todos/${id}/threads?threadId=${encodeURIComponent(threadId)}`);
  }

  schedules(): Promise<Schedule[]> {
    return this.request('GET', '/api/schedules');
  }

  createSchedule(input: {
    name?: string;
    agent: string;
    cwd: string;
    prompt: string;
    cron: string;
    mode: 'new' | 'same';
    threadId?: string | null;
    permissionMode?: string;
    enabled?: boolean;
    timezone?: string | null;
    catchUp?: boolean;
  }): Promise<Schedule> {
    return this.request('POST', '/api/schedules', input);
  }

  updateSchedule(id: string, patch: Record<string, unknown>): Promise<Schedule> {
    return this.request('PATCH', `/api/schedules/${id}`, patch);
  }

  deleteSchedule(id: string): Promise<void> {
    return this.request('DELETE', `/api/schedules/${id}`);
  }

  /** Fires now, whatever the clock says and whether the schedule is enabled. */
  runSchedule(id: string): Promise<{ status: string; message?: string }> {
    return this.request('POST', `/api/schedules/${id}/run`);
  }

  scheduleRuns(id: string): Promise<ScheduleRun[]> {
    return this.request('GET', `/api/schedules/${id}/runs`);
  }

  /** What a cron expression means: for the form's live preview. */
  previewCron(cron: string, timezone?: string | null): Promise<{ description: string; fires: string[] }> {
    return this.request('POST', '/api/schedules/preview', { cron, ...(timezone ? { timezone } : {}) });
  }

  thread(id: string): Promise<Thread> {
    return this.request('GET', `/api/threads/${id}`);
  }

  events(id: string, after = 0): Promise<StoredEvent[]> {
    return this.request('GET', `/api/threads/${id}/events?after=${after}`);
  }

  /** `forward` reads the eight turns after `after`, from the conversation's beginning on; otherwise the newest eight before `before`. */
  historyPage(id: string, before?: number, after?: number, chain = false, signal?: AbortSignal, forward = false): Promise<HistoryPage> {
    const params = new URLSearchParams();
    if (before) params.set('before', String(before));
    if (after) params.set('after', String(after));
    if (chain) params.set('chain', '1');
    if (forward) params.set('forward', '1');
    return this.request('GET', `/api/threads/${id}/history${params.size ? `?${params}` : ''}`, undefined, { signal });
  }

  historyHandover(id: string, full = false): Promise<{ source: { sourceId: string; agent: string; title: string }; method: 'resume' | 'summary' | null; turnId: string | null; reason: string | null; size: number; preview: string; text?: string }> {
    return this.request('GET', `/api/threads/${id}/history/handover${full ? '?text=1' : ''}`);
  }

  historySummary(id: string, turn: string, cursor?: number, version?: number): Promise<{ prompt: string; text: string; snapshotSeq: number; nextOffset: number | null }> {
    return this.request('GET', `/api/threads/${id}/history/summary?turn=${encodeURIComponent(turn)}${cursor ? `&cursor=${cursor}&version=${version}` : ''}`);
  }

  historyPosition(id: string): Promise<{ snapshotSeq: number }> {
    return this.request('GET', `/api/threads/${id}/history?head=1`);
  }

  historyWork(id: string, ref: DetailRef, cursor?: number, chain = false): Promise<HistoryWork> {
    return this.request('GET', `/api/threads/${id}/history/work?${chain ? 'chain=1&' : ''}turn=${encodeURIComponent(ref.turnId)}&id=${ref.id}${cursor ? `&cursor=${cursor}&version=${ref.version}` : ''}`);
  }

  historyDetail(id: string, ref: DetailRef, cursor?: number, path?: string): Promise<HistoryDetail> {
    return this.request('GET', `/api/threads/${id}/history/detail?turn=${encodeURIComponent(ref.turnId)}&id=${ref.id}${cursor ? `&cursor=${cursor}&version=${ref.version}` : ''}${path === undefined ? '' : `&path=${encodeURIComponent(path)}`}`);
  }

  historyToolOutput(id: string, ref: DetailRef): Promise<{ output: string }> {
    return this.request('GET', `/api/threads/${id}/history/tool-output?turn=${encodeURIComponent(ref.turnId)}&id=${ref.id}`);
  }

  /** A thread's whole log, with streamed fragments merged: for copying the conversation out. */
  allEvents(id: string): Promise<StoredEvent[]> {
    return this.request('GET', `/api/threads/${id}/events?all=1`);
  }

  /**
   * The newest turns of a thread: what a reopened page renders before any history is paged in.
   * Pages begin where a turn begins, and streamed fragments come merged.
   */
  eventsTail(id: string): Promise<StoredEvent[]> {
    return this.request('GET', `/api/threads/${id}/events?tail=1`);
  }

  /** The page of turns just before the given sequence number. */
  eventsBefore(id: string, before: number): Promise<StoredEvent[]> {
    return this.request('GET', `/api/threads/${id}/events?before=${before}`);
  }

  createThread(input: {
    workMode?: WorkMode;
    speed?: string | null;
    agent: string;
    cwd: string;
    title?: string;
    permissionMode?: string;
    model?: string | null;
    effort?: string | null;
    sectionId?: string | null;
    /** Start in a new git worktree under the repository's `.worktrees/`. */
    worktree?: boolean;
    /** In no project: the hub gives the thread a scratch folder of its own; `cwd` is ignored. */
    scratch?: boolean;
  }): Promise<Thread> {
    return this.request('POST', '/api/threads', input);
  }

  /** Whether a new thread in this directory could start in a git worktree of its own. */
  worktreeOptions(cwd: string): Promise<ForkOptions['worktree']> {
    return this.request('GET', `/api/worktree-options?cwd=${encodeURIComponent(cwd)}`);
  }

  teams(): Promise<TeamSummary[]> { return this.request('GET', '/api/teams'); }
  team(id: string): Promise<TeamDefinition> { return this.request('GET', `/api/teams/${id}`); }
  teamTemplate(): Promise<TeamDefinition> { return this.request('GET', '/api/teams/template'); }
  saveTeam(value: TeamDefinition): Promise<TeamDefinition> { return this.request(value.id ? 'PUT' : 'POST', `/api/teams${value.id ? '/' + value.id : ''}`, value); }
  deleteTeam(id: string): Promise<void> { return this.request('DELETE', `/api/teams/${id}`); }
  importTeam(value: unknown): Promise<TeamDefinition> { return this.request('POST', '/api/teams/import', value); }
  exportTeam(id: string): Promise<TeamDefinition> { return this.request('GET', `/api/teams/${id}/export`); }
  dispatchTeam(id: string, text: string, cwd: string, images: OutgoingImage[] = []): Promise<{ taskId: string; threadId: string }> { return this.request('POST', `/api/teams/${id}/tasks`, { text, ...(images.length ? { images } : {}), cwd, source: { kind: 'web' } }); }

  coordination(id: string): Promise<Coordination | null> { return this.request('GET', `/api/threads/${id}/coordination`); }
  /** Turns of any thread on this machine, archived ones too, in which the words were said. */
  search(query: string): Promise<SearchHit[]> {
    return this.request('GET', `/api/search?q=${encodeURIComponent(query)}`);
  }

  /** The daily digest of what was asked and what was called, newest day last. */
  activity(days = 14): Promise<ActivityReport> {
    return this.request('GET', `/api/activity?days=${days}`);
  }

  /** One turn of a thread as a trace; the last turn when none is named. */
  trace(threadId: string, turnId?: string): Promise<TurnTrace> {
    return this.request('GET', `/api/threads/${threadId}/trace${turnId ? `?turn=${encodeURIComponent(turnId)}` : ''}`);
  }

  /** What the thread produced over all its turns: changed files, pictures and files. Names and counts only. */
  outputs(threadId: string): Promise<ThreadOutputs> {
    return this.request('GET', `/api/threads/${threadId}/outputs`);
  }

  /** The thread's session statistics: summary numbers only, timing from per-turn storage. */
  stats(threadId: string): Promise<ThreadStats> {
    return this.request('GET', `/api/threads/${threadId}/stats`);
 }

  /** The whole prompt a thread's conversation runs with, as the agent itself persisted it. */
  systemPrompt(threadId: string): Promise<SystemPrompt> {
    return this.request('GET', `/api/threads/${threadId}/system-prompt`);
  }

  configureCoordination(id: string, body: { action?: string; directories?: string[]; defaults?: Record<string, MemberDefault>; permission?: string }): Promise<Coordination> {
    return this.request('POST', `/api/threads/${id}/coordination`, body);
  }

  /** Sends a message. With `queue`, one sent while a turn is running waits for that turn to finish. */
  startTurn(
    threadId: string,
    text: string,
    images: OutgoingImage[] = [],
    opts: { queue?: boolean; collaboration?: { directories?: string[]; defaults?: Record<string, MemberDefault>; permission?: string }; mentions?: AgentMention[]; videos?: string[]; workMode?: WorkMode } = {},
  ): Promise<{ turnId: string } | { queued: true; id: string }> {
    return this.request('POST', `/api/threads/${threadId}/turns`, {
      text,
      ...(images.length ? { images } : {}),
      ...(opts.queue ? { queue: true } : {}),
      ...(opts.collaboration ? { collaboration: opts.collaboration } : {}),
      ...(opts.mentions?.length ? { mentions: opts.mentions } : {}),
      ...(opts.videos?.length ? { videos: opts.videos } : {}),
      ...(opts.workMode ? { workMode: opts.workMode } : {}),
    });
  }

  /**
   * Edits an earlier message of the user's and sends it again from there. The page reloads the
   * conversation the daemon keeps; the old turns stay out of it.
   */
  editTurn(
    threadId: string,
    turnId: string,
    input: { text: string; images: string[]; revision: number; requestId: string },
  ): Promise<{ turnId: string; revision: number }> {
    return this.request('POST', `/api/threads/${threadId}/turns/${turnId}/edit`, input);
  }

  /** Takes a waiting message out of the queue; it comes back whole, so it can be edited. */
  removeQueued(threadId: string, queuedId: string): Promise<{ text: string; images: OutgoingImage[]; videos?: VideoRef[]; mentions?: AgentMention[] }> {
    return this.request('DELETE', `/api/threads/${threadId}/queue/${queuedId}`);
  }

  /**
   * Binary upload of a video or any other file, shared across retries of the same file in the same
   * thread. Either way the message lists it under `videos`.
   */
  uploadVideo(threadId: string, file: File): Promise<VideoRef> {
    const uploads = this.videoUploads.get(file) ?? new Map<string, Promise<VideoRef>>();
    this.videoUploads.set(file, uploads);
    const existing = uploads.get(threadId);
    if (existing) return existing;
    const pending = (async () => {
      const video = isSupportedVideo(file);
      const res = await fetch(this.url(`/api/threads/${threadId}/${video ? 'videos' : 'files'}`), { method: 'POST', headers: this.headers({ 'Content-Type': file.type || 'application/octet-stream', 'X-File-Name': encodeURIComponent(file.name) }), body: file });
      if (res.status === 401) throw await authError(res);
      const json = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(res.status === 404 ? t(video ? 'api.noVideos' : 'api.noFiles') : json.error ?? `HTTP ${res.status}`, json, res.status);
      return json as VideoRef;
    })();
    uploads.set(threadId, pending);
    pending.catch(() => uploads.delete(threadId));
    return pending;
  }

  async videoFile(threadId: string, video: VideoRef): Promise<File> {
    const file = new File([await this.attachment(threadId, video.id)], video.name, { type: video.mimeType });
    this.videoUploads.set(file, new Map([[threadId, Promise.resolve(video)]]));
    return file;
  }

  /** Interrupts the running turn and sends this waiting message next. */
  sendQueuedNow(threadId: string, queuedId: string): Promise<void> {
    return this.request('POST', `/api/threads/${threadId}/queue/${queuedId}/now`);
  }

  /** An image file in the thread's directory that the agent made or mentioned. Rejects if there is none. */
  async threadFile(threadId: string, path: string): Promise<Blob> {
    const res = await fetch(this.url(`/api/threads/${threadId}/files?path=${encodeURIComponent(path)}`), { headers: this.headers() });
    if (res.status === 401) throw await authError(res);
    if (!res.ok) throw new ApiError(`HTTP ${res.status}`, {}, res.status);
    return res.blob();
  }

  gitStatus(threadId: string, signal?: AbortSignal): Promise<GitStatus> {
    return this.request('GET', `/api/threads/${threadId}/git/status`, undefined, { signal });
  }

  gitLog(threadId: string, head?: string | null, skip = 0, signal?: AbortSignal): Promise<GitLog> {
    const query = new URLSearchParams({ skip: String(skip), ...(head ? { head } : {}) });
    return this.request('GET', `/api/threads/${threadId}/git/log?${query}`, undefined, { signal });
  }

  gitCommit(threadId: string, commit: string, signal?: AbortSignal): Promise<GitCommitFiles> {
    return this.request('GET', `/api/threads/${threadId}/git/commit?${new URLSearchParams({ commit })}`, undefined, { signal });
  }

  gitDiff(threadId: string, path: string, staged: boolean, commit?: string, signal?: AbortSignal): Promise<GitDiff> {
    const query = new URLSearchParams({ path, staged: staged ? '1' : '0', ...(commit ? { commit } : {}) });
    return this.request('GET', `/api/threads/${threadId}/git/diff?${query}`, undefined, { signal });
  }

  gitOperate(threadId: string, operation: 'fetch' | 'pull'): Promise<GitOperation> {
    return this.request('POST', `/api/threads/${threadId}/git/${operation}`, {});
  }

  /** One folder of the thread's directory, for the file browser. No path: the directory itself. */
  threadTree(threadId: string, path?: string): Promise<FolderListing> {
    return this.request('GET', `/api/threads/${threadId}/tree${path ? `?path=${encodeURIComponent(path)}` : ''}`);
  }

  /** The folders the file browser can stand on: the thread's own, the agent's session folders, the project directories, the home directory. */
  threadRoots(threadId: string): Promise<{ roots: Array<{ path: string; kind: 'thread' | 'session' | 'project' | 'home' }> }> {
    return this.request('GET', `/api/threads/${threadId}/roots`);
  }

  /** A text file in the thread's directory. A binary one comes back with `text: null`. */
  threadText(threadId: string, path: string): Promise<TextFile> {
    return this.request('GET', `/api/threads/${threadId}/text?path=${encodeURIComponent(path)}`);
  }

  /** Starts writing mode, or moves it to another stage. */
  setWriting(threadId: string, stage: WritingStage): Promise<Thread> {
    return this.request('PUT', `/api/threads/${threadId}/writing`, { stage });
  }

  /** Ends writing mode; the body stays where it is. */
  endWriting(threadId: string): Promise<Thread> {
    return this.request('DELETE', `/api/threads/${threadId}/writing`);
  }

  /**
   * Saves a writing-mode body. Refused with 409 (`details.hash`: what is on disk now) when the file
   * changed since `baseHash` was read.
   */
  saveWritingText(threadId: string, path: string, text: string, baseHash: string): Promise<{ hash: string }> {
    return this.request('PUT', `/api/threads/${threadId}/writing/text?path=${encodeURIComponent(path)}`, { text, baseHash });
  }

  /** Files under a browse root whose path has every word of the query, relative to that root. */
  findFiles(threadId: string, query: string, path?: string): Promise<{ root: string; paths: string[]; truncated: boolean }> {
    return this.request('GET', `/api/threads/${threadId}/find?q=${encodeURIComponent(query)}${path ? `&path=${encodeURIComponent(path)}` : ''}`);
  }

  /**
   * A short-lived, signed address for one of the thread's files, for a `<video>` tag or a save
   * link: neither can send the token as a header. `save` asks for the save prompt rather than an
   * inline open, and only matters for a type the browser would otherwise play or show.
   */
  downloadLink(threadId: string, path: string, save = true): Promise<{ url: string; name: string; size: number; mimeType: string }> {
    return this.request<{ url: string; name: string; size: number; mimeType: string }>(
      'GET',
      `/api/threads/${threadId}/download-link?path=${encodeURIComponent(path)}${save ? '&dl=1' : ''}`,
    ).then((link) => ({ ...link, url: this.url(link.url) }));
  }

  /** An image attached to a turn. Fetched with the token, since an <img> tag cannot send it. */
  async attachment(threadId: string, attachmentId: string): Promise<Blob> {
    const res = await fetch(this.url(`/api/threads/${threadId}/attachments/${attachmentId}`), { headers: this.headers() });
    if (res.status === 401) throw await authError(res);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.blob();
  }

  /** A remote member's result file, kept by the hub; fetched with the token and then saved by the page. */
  async coordinationResult(threadId: string, deliveryId: string): Promise<Blob> {
    const res = await fetch(this.url(`/api/threads/${threadId}/coordination/result/${encodeURIComponent(deliveryId)}`), { headers: this.headers() });
    if (res.status === 401) throw await authError(res);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.blob();
  }

  /** What a thread that wants the person comes down to, read from its log without a model. */
  threadBrief(threadId: string): Promise<ThreadBrief> {
    return this.request('GET', `/api/threads/${threadId}/brief`);
  }

  /** Sends the ask the thread stopped on again: the person's "continue" on a thread whose last turn failed. */
  retryThread(threadId: string): Promise<Thread> {
    return this.request('POST', `/api/threads/${threadId}/retry`);
  }

  /** Stops the running turn. Messages that were waiting are not sent; their text comes back. */
  cancel(threadId: string): Promise<{ dropped: string[]; mentions?: AgentMention[] }> {
    return this.request('POST', `/api/threads/${threadId}/cancel`);
  }

  async decide(approvalId: string, decision: { optionId: string } | { cancel: true }): Promise<void> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15_000);
    try {
      await this.request('POST', `/api/approvals/${approvalId}`, decision, { signal: controller.signal });
    } catch (e) {
      if (controller.signal.aborted) throw new Error(t('sheet.approval.timeout'));
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  answerInput(id: string, answer: UserInputAnswer): Promise<void> {
    return this.request('POST', `/api/inputs/${id}`, answer);
  }

  updateThread(
    threadId: string,
    updates: {
      rememberModelChoice?: boolean;
      workMode?: WorkMode;
      speed?: string | null;
      title?: string;
      pinned?: boolean;
      permissionMode?: string;
      /** null returns the setting to the agent's default. */
      model?: string | null;
      effort?: string | null;
      archived?: boolean;
      /** null takes the thread out of its section. */
      sectionId?: string | null;
      unread?: boolean;
      done?: boolean;
      /** Places the thread on the board by hand; clears done. */
      stage?: ProgressReport['stage'];
    },
  ): Promise<Thread> {
    return this.request('PATCH', `/api/threads/${threadId}`, updates);
  }

  /** Deletes for good. With `agentSession`, the agent's own copy of the conversation goes too. */
  /**
   * `worktree` removes the git worktree the thread runs in as well. Work that exists nowhere else
   * stops that with an ApiError whose details say how much (`code: 'worktree_dirty'`), until `force`.
   */
  deleteThread(threadId: string, opts: { agentSession?: boolean; worktree?: boolean; force?: boolean } = {}): Promise<void> {
    const query = [opts.agentSession && 'agentSession=1', opts.worktree && 'worktree=1', opts.force && 'force=1'].filter(Boolean).join('&');
    return this.request('DELETE', `/api/threads/${threadId}${query ? `?${query}` : ''}`);
  }

  /** Sessions the agent has in the allowed directories, newest first. */
  agentSessions(agent: string): Promise<AgentSession[]> {
    return this.request('GET', `/api/agents/${agent}/sessions`);
  }

  /** Makes a thread out of an existing agent session, history included. */
  importSession(agent: string, sessionId: string): Promise<Thread> {
    return this.request('POST', '/api/threads/import', { agent, sessionId });
  }

  /** This machine's agent sessions that are not threads here, newest first, one page at a time. */
  localSessions(cursor?: string | null, limit = 60): Promise<LocalSessionPage> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (cursor) query.set('cursor', cursor);
    return this.request('GET', `/api/local-sessions?${query}`);
  }

  /** This machine's tokens by minute, model and launcher, read off the agents' transcripts. */
  machineUsage(hours: 1 | 24 | 168, model?: string | null, launcher?: string | null): Promise<MachineUsageReport> {
    const query = new URLSearchParams({ hours: String(hours) });
    if (model) query.set('model', model);
    // An empty launcher is a session the hub did not start; the daemon reads `-` as that too.
    if (launcher != null) query.set('launcher', launcher === '' ? '-' : launcher);
    return this.request('GET', `/api/machine-usage?${query}`);
  }

  /**
   * Copies a thread, on the agent's side too, into a new one that can go its own way. With a turn,
   * the copy ends where that turn ended.
   */
  /** With `turnId` the copy ends where that turn ended; with `worktree` it runs in a git worktree of its own. */
  forkThread(threadId: string, opts: { turnId?: string; worktree?: boolean } = {}): Promise<Thread> {
    const body = { ...(opts.turnId ? { turnId: opts.turnId } : {}), ...(opts.worktree ? { worktree: true } : {}) };
    return this.request('POST', `/api/threads/${threadId}/fork`, Object.keys(body).length ? body : undefined);
  }

  /** What forking this thread can offer besides a copy in the same directory. */
  forkOptions(threadId: string): Promise<ForkOptions> {
    return this.request('GET', `/api/threads/${threadId}/fork-options`);
  }

  /** Pulls in turns the terminal added to this thread's session. */
  syncThread(threadId: string): Promise<{ added: number; terminalActivity: boolean }> {
    return this.request('POST', `/api/threads/${threadId}/sync`);
  }

  /** A short-lived signed URL for one file of the thread's directory, for a preview iframe. */
  previewLink(threadId: string, path: string): Promise<{ url: string }> {
    return this.request('GET', `/api/threads/${threadId}/preview-link?path=${encodeURIComponent(path)}`);
  }

  previewNotes(threadId: string, page: string): Promise<PreviewNote[]> { return this.request('GET', `/api/threads/${threadId}/preview-notes?page=${encodeURIComponent(page)}`); }
  addPreviewNote(threadId: string, page: string, pick: PreviewPick, comment: string): Promise<PreviewNote> { return this.request('POST', `/api/threads/${threadId}/preview-notes`, { page, ...pick, comment }); }
  removePreviewNote(threadId: string, id: string): Promise<void> { return this.request('DELETE', `/api/threads/${threadId}/preview-notes/${id}`); }
  sendPreviewNotes(threadId: string, page: string, ids: string[]): Promise<{ turnId: string } | { queued: true; id: string }> { return this.request('POST', `/api/threads/${threadId}/preview-notes/send`, { page, ids }); }

  /** Creates an empty successor in the same directory and worktree, without starting an agent turn. */
  handoverSettings(): Promise<HandoverSettings> { return this.request('GET', '/api/handover-settings'); }
  saveHandoverSettings(value: HandoverSettings): Promise<HandoverSettings> { return this.request('PUT', '/api/handover-settings', value); }
  autoCompactSettings(): Promise<{ settings: AutoCompactSettings; status: AutoCompactStatus }> { return this.request('GET', '/api/autocompact-settings'); }
  saveAutoCompactSettings(value: AutoCompactSettings): Promise<AutoCompactSettings> { return this.request('PUT', '/api/autocompact-settings', value); }
  autoHandover(id: string): Promise<AutoHandoverView & { targets: HandoverTarget[] }> { return this.request('GET', `/api/threads/${id}/auto-handover`); }
  /** The thread's native goal, for the goal card above the input. */
  threadGoal(id: string): Promise<{ goal: GoalView | null }> { return this.request('GET', `/api/threads/${id}/goal`); }
  setAutoHandover(id: string, enabled: boolean | null): Promise<AutoHandoverView> { return this.request('PATCH', `/api/threads/${id}/auto-handover`, { enabled }); }
  setResume(id: string, armed: boolean): Promise<AutoHandoverView> { return this.request('PATCH', `/api/threads/${id}/auto-handover`, { resume: armed }); }
  /** Cancels the daemon's automatic retry of a transient failure; arming itself is automatic. */
  cancelAutoRetry(id: string): Promise<Thread> { return this.request('PATCH', `/api/threads/${id}/auto-retry`, { enabled: false }); }
  cancelAutoHandover(id: string): Promise<AutoHandoverView> { return this.request('DELETE', `/api/threads/${id}/auto-handover`); }
  setCollaborationHandover(id: string, enabled: boolean): Promise<Coordination> { return this.request('PATCH', `/api/threads/${id}/collaboration-handover`, { enabled }); }

  createHandoverThread(threadId: string, agent: string, model?: string, mode?: 'continue' | 'branch', takeTeam?: boolean): Promise<Thread> {
    return this.request('POST', `/api/threads/${threadId}/handover-thread`, { agent, model, mode, ...(takeTeam === false ? { takeTeam: false } : {}) });
  }
  /** Hands the thread to `target` now: its conversation, queue and any collaboration lead. */
  takeOver(threadId: string, target: string | HandoverTarget, note?: string): Promise<Thread> {
    const body: HandoverTarget & { note?: string } = typeof target === 'string' ? { agent: target } : { ...target };
    if (note !== undefined) body.note = note;
    return this.request('POST', `/api/threads/${threadId}/takeover`, body);
  }
  detachAutomaticHandover(threadId: string): Promise<{ detached: string[]; thread: Thread }> {
    return this.request('DELETE', `/api/threads/${threadId}/handover-link`);
  }

  /** Asks the thread's agent to write a handover summary for another agent, in its own session. */
  startHandover(threadId: string, target: string): Promise<{ turnId: string }> {
    return this.request('POST', `/api/threads/${threadId}/handover`, { target });
  }

  /** The handover summary: `{ pending: true }` while the handover turn is still running. */
  handoverSummary(
    threadId: string,
    turn = '',
    target = '',
  ): Promise<{ pending: true } | { summary: string; source: 'agent' | 'log' }> {
    const q = `turn=${encodeURIComponent(turn)}&target=${encodeURIComponent(target)}`;
    return this.request('GET', `/api/threads/${threadId}/handover?${q}`);
  }

  /** CLIs on PATH that look like another copy of a known agent, offered for adding. */
  discoverAgents(): Promise<Array<{ id: string; adapter: string; command: string; label?: string; icon?: string }>> {
    return this.request('GET', '/api/agents/discover');
  }

  /** Agent adapters supported by the hub, including install instructions for missing ones. */
  supportedAgents(): Promise<SupportedAgent[]> {
    return this.request('GET', '/api/agents/supported');
  }

  /** Accounts of multi-account agents already signed in on this host, ready to link in one click. */
  discoverAccounts(): Promise<Array<{ adapter: string; email: string; env: Record<string, string>; id: string; label: string }>> {
    return this.request('GET', '/api/agents/accounts/discover');
  }

  /** Adds an agent instance (another CLI, another account). */
  addAgent(input: { id?: string; adapter: string; label?: string; command?: string[]; env?: Record<string, string>; account?: string; streamBridge?: boolean }): Promise<{ id: string }> {
    return this.request('POST', '/api/agents', input);
  }

  /** Updates hub-owned settings; its id, which threads store, does not change. */
  updateAgent(id: string, patch: { label?: string; hidden?: boolean; streamBridge?: boolean }): Promise<{ id: string; label?: string; hidden?: boolean }> {
    return this.request('PATCH', `/api/agents/${encodeURIComponent(id)}`, patch);
  }

  /** Whether the experimental bridge would run on this install right now, and why not. */
  agentStreamBridge(id: string): Promise<{ compatible: boolean; reason?: string }> {
    return this.request('GET', `/api/agents/${encodeURIComponent(id)}/stream-bridge`);
  }

  /** Removes only the hub's registration; the agent's config, login and sessions stay. */
  removeAgent(id: string): Promise<void> {
    return this.request('DELETE', `/api/agents/${encodeURIComponent(id)}`);
  }

  /** Threads, schedules, teams and handover orders that would block removal. */
  agentReferences(id: string): Promise<AgentReferences> {
    return this.request('GET', `/api/agents/${encodeURIComponent(id)}/references`);
  }

  /** Starts this instance's CLI login in a web terminal, in its own account environment. */
  loginAgentTerminal(id: string): Promise<{ terminalId: string }> {
    return this.request('POST', `/api/agents/${encodeURIComponent(id)}/login-terminal`);
  }

  createAgentAccount(input: { adapter: string; sourceId?: string; label?: string }): Promise<{ id: string }> {
    return this.request('POST', '/api/agents/accounts', input);
  }

  startAgentLogin(agent: string): Promise<AgentLogin> {
    return this.request('POST', `/api/agents/${encodeURIComponent(agent)}/login`);
  }

  agentLogin(agent: string, id: string): Promise<AgentLogin> {
    return this.request('GET', `/api/agents/${encodeURIComponent(agent)}/login/${encodeURIComponent(id)}`);
  }

  submitAgentLogin(agent: string, id: string, code: string): Promise<AgentLogin> {
    return this.request('POST', `/api/agents/${encodeURIComponent(agent)}/login/${encodeURIComponent(id)}`, { code });
  }

  cancelAgentLogin(agent: string, id: string): Promise<void> {
    return this.request('DELETE', `/api/agents/${encodeURIComponent(agent)}/login/${encodeURIComponent(id)}`);
  }

  /** Commands for an agent in a directory; project-level commands differ between directories. */
  commands(agent: string, cwd?: string): Promise<SlashCommand[]> {
    return this.request('GET', `/api/agents/${agent}/commands${cwd ? `?cwd=${encodeURIComponent(cwd)}` : ''}`);
  }

  /** Plan usage of an agent instance, read outside any session. `refresh` asks again now. */
  usageReport(hours = 24, agent = ''): Promise<UsageReport> {
    return this.request('GET', `/api/usage?hours=${hours}&agent=${encodeURIComponent(agent)}`);
  }
  usageCosts(hours = 24, agent = ''): Promise<CostReport> {
    return this.request('GET', `/api/usage/costs?hours=${hours}&agent=${encodeURIComponent(agent)}`);
  }
  /** Tokens per local day over the last year, for the activity heatmap. */
  usageDaily(agent = ''): Promise<DailyUsage> {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return this.request('GET', `/api/usage/daily?agent=${encodeURIComponent(agent)}&tz=${encodeURIComponent(tz)}`);
  }

  agentDiagnostic(agent: string, refresh = false): Promise<AgentDiagnostic> {
    return this.request('GET', `/api/agents/${encodeURIComponent(agent)}/diagnostics${refresh ? '?refresh=1' : ''}`);
  }

  /** A previous reading for immediate display, even after reopening the board or reloading the page. */
  cachedAgentQuota(agent: string): PlanQuota | null {
    return this.quotas.get(agent) ?? (loadToken() === this.token
      ? cachedQuota(this.url(`/api/agents/${encodeURIComponent(agent)}/quota`)) : null);
  }

  /** A reading the hub pushed on its stream: kept as if it had just been asked for. */
  rememberAgentQuota(agent: string, quota: PlanQuota): void {
    this.quotas.set(agent, quota);
    if (loadToken() === this.token) rememberQuota(this.url(`/api/agents/${encodeURIComponent(agent)}/quota`), quota);
  }

  agentQuota(agent: string, refresh = false): Promise<PlanQuota> {
    const pending = this.quotaRequests.get(agent);
    if (pending) return pending;
    const path = `/api/agents/${encodeURIComponent(agent)}/quota`;
    const request = this.request<PlanQuota>('GET', `${path}${refresh ? '?refresh=1' : ''}`).then(quota => {
      this.quotas.set(agent, quota);
      // A request that finishes after sign-out must not repopulate the next login's cache.
      if (loadToken() === this.token) rememberQuota(this.url(path), quota);
      return quota;
    }).finally(() => this.quotaRequests.delete(agent));
    this.quotaRequests.set(agent, request);
    return request;
  }

  // Skills, in the folders the agents themselves read. Whichever machine this client is for
  // answers from its own folders; the skills page also opens one client per other machine.

  skills(): Promise<SkillsListing> {
    return this.request('GET', '/api/skills');
  }

  /** One skill and its SKILL.md, of the copy in `path` when that is given. */
  skillDetail(name: string, path?: string | null): Promise<SkillDetail> {
    const q = `name=${encodeURIComponent(name)}${path ? `&path=${encodeURIComponent(path)}` : ''}`;
    return this.request('GET', `/api/skills/detail?${q}`);
  }

  /** A skill packed to carry to another machine. */
  skillExport(name: string, path?: string | null): Promise<SkillPayload> {
    const q = `name=${encodeURIComponent(name)}${path ? `&path=${encodeURIComponent(path)}` : ''}`;
    return this.request('GET', `/api/skills/export?${q}`);
  }

  /** Copies a skill into agents' folders: from a folder on this machine, or from a payload. */
  skillInstall(body: {
    from?: { name: string; path: string };
    skill?: SkillPayload;
    targets: string[];
    overwrite?: boolean;
  }): Promise<SkillInstallOutcome[]> {
    return this.request('POST', '/api/skills/install', body);
  }

  /** Takes a skill out of one folder on this machine. */
  skillRemove(name: string, path: string): Promise<void> {
    return this.request('DELETE', `/api/skills?name=${encodeURIComponent(name)}&path=${encodeURIComponent(path)}`);
  }

  // MCP, in each agent's own config. Whichever machine this client is for scans its own agents;
  // entries (the notes and manual servers) live on the hub the page talks to.

  /** This machine's scan: every agent instance whose MCP config the daemon can read. */
  mcp(): Promise<McpListing> {
    return this.request('GET', '/api/mcp');
  }

  /** Writes one server into each listed instance's config on this machine. */
  mcpInstall(body: { targets: string[]; name: string; config: Record<string, unknown>; overwrite?: boolean }): Promise<McpInstallOutcome[]> {
    return this.request('POST', '/api/mcp/install', body);
  }

  /** Takes one server out of one instance's config on this machine. */
  mcpRemove(agent: string, name: string): Promise<void> {
    return this.request('DELETE', `/api/mcp?agent=${encodeURIComponent(agent)}&name=${encodeURIComponent(name)}`);
  }

  prepareExtension(origin: string, sha256?: string): Promise<ExtensionPackagePreview> {
    return this.request('POST', '/api/extensions/prepare', { origin, sha256 });
  }
  commitExtension(token: string, granted: Permission[]): Promise<ExtensionState> {
    return this.request('POST', '/api/extensions/commit', { token, granted });
  }
  extensionConfiguration(id: string): Promise<ExtensionConfiguration> {
    return this.request('GET', `/api/extensions/${encodeURIComponent(id)}/configuration`);
  }
  configureExtension(id: string, config: ExtensionConfiguration): Promise<ExtensionConfiguration> {
    return this.request('PUT', `/api/extensions/${encodeURIComponent(id)}/configuration`, config);
  }
  detectExtension(id: string): Promise<ExtensionDetection> {
    return this.request('POST', `/api/extensions/${encodeURIComponent(id)}/detect`);
  }
  rollbackExtension(id: string): Promise<ExtensionState> {
    return this.request('POST', `/api/extensions/${encodeURIComponent(id)}/rollback`);
  }
  workSources(options: { consumer?: string; threadId?: string; refresh?: boolean } = {}): Promise<WorkProjection[]> {
    const params = new URLSearchParams();
    if (options.consumer) params.set('consumer', options.consumer);
    if (options.threadId) params.set('threadId', options.threadId);
    if (options.refresh) params.set('refresh', '1');
    return this.request('GET', `/api/work-sources?${params}`);
  }

  /** LoopX extensions this daemon ships, and which are installed on its machine. */
  autotitleSettings(): Promise<{ settings: import('@calli/extension-api/web').AutoTitleSettings; status: import('@calli/extension-api/web').AutoTitleStatus; pass: import('@calli/extension-api/web').AutoTitlePass | null; defaultRules: string }> {
    return this.request('GET', '/api/extensions/autotitle/settings');
  }
  saveAutotitleSettings(patch: Partial<import('@calli/extension-api/web').AutoTitleSettings>): Promise<import('@calli/extension-api/web').AutoTitleSettings> {
    return this.request('PUT', '/api/extensions/autotitle/settings', patch);
  }
  previewAutotitle(threadId: string): Promise<{ title: string }> {
    return this.request('POST', '/api/extensions/autotitle/preview', { threadId });
  }
  organizeAutotitle(): Promise<{ queued: number }> {
    return this.request('POST', '/api/extensions/autotitle/organize');
  }
  /** The person's "name it again": the auto-titles extension names the thread now. */
  autotitle(threadId: string): Promise<{ title: string }> {
    return this.request('POST', `/api/threads/${encodeURIComponent(threadId)}/autotitle`);
  }
  memorySources(extensionId: string): Promise<import('@calli/extension-api').MemorySource[]> {
    return this.request('GET', `/api/extensions/${encodeURIComponent(extensionId)}/memory/sources`);
  }
  memoryList(extensionId: string, sourceId: string): Promise<import('@calli/extension-api').MemoryIndex> {
    return this.request('GET', `/api/extensions/${encodeURIComponent(extensionId)}/memory/${encodeURIComponent(sourceId)}/files`);
  }
  memoryRead(extensionId: string, sourceId: string, fileId: string): Promise<import('@calli/extension-api').MemoryDocument> {
    return this.request('GET', `/api/extensions/${encodeURIComponent(extensionId)}/memory/${encodeURIComponent(sourceId)}/document?id=${encodeURIComponent(fileId)}`);
  }

  extensions(): Promise<ExtensionState[]> {
    return this.request('GET', '/api/extensions');
  }

  /** Installs with the permissions the user confirmed, which must be exactly the ones the extension asks for. */
  installExtension(id: string, granted: Permission[]): Promise<ExtensionState> {
    return this.request('POST', `/api/extensions/${encodeURIComponent(id)}/install`, { granted });
  }

  uninstallExtension(id: string): Promise<void> {
    return this.request('DELETE', `/api/extensions/${encodeURIComponent(id)}`);
  }

  setExtensionEnabled(id: string, enabled: boolean): Promise<ExtensionState> {
    return this.request('PATCH', `/api/extensions/${encodeURIComponent(id)}`, { enabled });
  }

  /** The registry of notes and manually registered servers, on this daemon. */
  mcpEntries(): Promise<McpEntry[]> {
    return this.request('GET', '/api/mcp/entries');
  }

  mcpSaveEntry(body: { name: string; businessLine?: string; description?: string; server?: Record<string, unknown> | null }): Promise<McpEntry> {
    return this.request('PUT', '/api/mcp/entries', body);
  }

  mcpDeleteEntry(name: string): Promise<void> {
    return this.request('DELETE', `/api/mcp/entries/${encodeURIComponent(name)}`);
  }

  /** Whether this machine shows a desktop: a VNC server was found on it. */
  desktop(): Promise<{ available: boolean; port?: number }> {
    return this.request('GET', '/api/desktop');
  }

  /**
   * The WebSocket address of this machine's desktop, with a ticket that opens it once. A WebSocket
   * cannot send the token, and the hub signs the ticket even for another machine: asked of the hub.
   */
  async desktopSocketUrl(): Promise<string> {
    const query = this.host ? `?host=${encodeURIComponent(this.host)}` : '';
    const { url } = await this.send<{ url: string }>('GET', `${this.base}/api/desktop/ticket${query}`);
    const address = new URL(this.base + url, location.href);
    address.protocol = address.protocol === 'https:' ? 'wss:' : 'ws:';
    return address.toString();
  }

  /** Live web terminals on this daemon. */
  listTerminals(): Promise<TerminalInfo[]> {
    return this.request('GET', '/api/terminals');
  }

  /** Starts a terminal in `cwd`, which must be one of the allowed workdirs. */
  createTerminal(cwd: string): Promise<TerminalInfo> {
    return this.request('POST', '/api/terminals', { cwd });
  }

  /** Sends keystrokes (or a paste) to a terminal. */
  terminalInput(id: string, data: string): Promise<void> {
    return this.request('POST', `/api/terminals/${id}/input`, { data });
  }

  terminalSize(id: string, cols: number, rows: number): Promise<void> {
    return this.request('POST', `/api/terminals/${id}/size`, { cols, rows });
  }

  /** Ends a terminal. Closing its panel does not; the shell is exited by typing, or reaped when idle. */
  killTerminal(id: string): Promise<void> {
    return this.request('DELETE', `/api/terminals/${id}`);
  }

  models(agent: string, refresh = false): Promise<ModelOption[]> {
    return this.request('GET', `/api/agents/${encodeURIComponent(agent)}/models${refresh ? '?refresh=1' : ''}`);
  }

  listDirectory(path: string): Promise<DirectoryInfo> {
    const encoded = encodeURIComponent(path);
    return this.request('GET', `/api/directories?path=${encoded}`);
  }

  createReference(input: { kind: 'thread'; sourceId: string } | { kind: 'directory'; path: string }
    | { kind: 'message'; text: string; title: string; path: string; sourceId?: string; turnId?: string }): Promise<ContextReference> {
    return this.request('POST', '/api/context-references', input);
  }

  projectDirectories(path?: string): Promise<ProjectDirectoryInfo> {
    return this.request('GET', `/api/project-directories${path ? `?path=${encodeURIComponent(path)}` : ''}`);
  }

  directoryTree(root: string, maxDepth = 3): Promise<DirectoryTreeNode> {
    const encoded = encodeURIComponent(root);
    return this.request('GET', `/api/directory-tree?root=${encoded}&maxDepth=${maxDepth}`);
  }

  /**
   * Opens an SSE stream and calls `onEvent` for each message. Resolves when the server closes the stream.
   * The caller aborts via `signal`. Uses fetch (not EventSource) so the Authorization header can be sent.
   */
  /**
   * Reads a server-sent event stream until it ends, and returns so the caller can reconnect. A
   * stream can also die without ending: a phone that slept or changed network leaves a read that
   * never resolves. The server writes at least a ping every 15 seconds, so a stream silent for
   * `idleMs` is taken for dead; and when the page comes back to the foreground, one that has been
   * silent for a few seconds is not waited on at all.
   */
  async stream<T = StoredEvent>(path: string, onEvent: (ev: T) => void, signal: AbortSignal, idleMs = STREAM_IDLE_MS, onOpen?: () => void): Promise<void> {
    const res = await fetch(this.url(path), { headers: this.headers({ Accept: 'text/event-stream' }), signal });
    if (res.status === 401) throw await authError(res);
    if (!res.ok || !res.body) throw new Error(`stream failed: HTTP ${res.status}`);
    onOpen?.();
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let heardAt = Date.now();
    const giveUp = () => void reader.cancel().catch(() => undefined);
    let idle = setTimeout(giveUp, idleMs);
    const onVisible = () => {
      if (!document.hidden && Date.now() - heardAt > STREAM_WAKE_MS) giveUp();
    };
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisible);
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        heardAt = Date.now();
        clearTimeout(idle);
        idle = setTimeout(giveUp, idleMs);
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const data = block
            .split('\n')
            .filter((l) => l.startsWith('data: '))
            .map((l) => l.slice(6))
            .join('\n');
          if (data) onEvent(JSON.parse(data) as T);
        }
      }
    } finally {
      clearTimeout(idle);
      if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisible);
    }
  }

  // ---------------------------------------------------------------- Realtime Voice

  private voiceHeaders(extra: Record<string, string> = {}): Record<string, string> {
    return this.headers({ 'X-LooChat-Voice-Owner': TAB_VOICE_OWNER, ...extra });
  }

  protected async sendVoice<T>(method: string, url: string, body?: unknown): Promise<T> {
    const res = await fetch(url, {
      method,
      headers: this.voiceHeaders(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 401) {
      const body = await res.json().catch(() => ({})) as { auth_error?: string };
      const code = body.auth_error === 'link_expired' || body.auth_error === 'link_revoked' ? body.auth_error : undefined;
      throw new AuthError('unauthorized', code);
    }
    if (res.status === 204) return undefined as T;
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError((json as { error?: string }).error ?? `HTTP ${res.status}`, json as Record<string, unknown>, res.status);
    return json as T;
  }

  /** Lists available native realtime voice providers on the machine. */
  voiceProviders(): Promise<{ providers: VoiceProvider[] }> {
    return this.sendVoice('GET', this.url('/api/voice/providers'));
  }

  /** Starts a realtime voice session with SDP offer and tab owner header. */
  startVoiceSession(threadId: string, offer: string, extensionId = 'voice'): Promise<VoiceSnapshot> {
    return this.sendVoice('POST', this.url('/api/voice'), { extensionId, threadId, offer });
  }

  /** Sends a voice session heartbeat (sent every 10 seconds). */
  voiceHeartbeat(sessionId: string): Promise<VoiceSnapshot> {
    return this.sendVoice('POST', this.url(`/api/voice/${encodeURIComponent(sessionId)}/heartbeat`));
  }

  /** Notifies the daemon that the WebRTC peer connection has connected. */
  voiceConnected(sessionId: string): Promise<VoiceSnapshot> {
    return this.sendVoice('POST', this.url(`/api/voice/${encodeURIComponent(sessionId)}/connected`));
  }

  appendVoiceText(sessionId: string, text: string, images: OutgoingImage[] = [], videos: string[] = []): Promise<void> {
    return this.sendVoice('POST', this.url(`/api/voice/${encodeURIComponent(sessionId)}/text`), { text, images, videos });
  }

  voiceHistory(threadId: string, sessionId: string, cursor?: number): Promise<{ events: StoredEvent[]; nextCursor: number | null }> {
    return this.request('GET', `/api/threads/${encodeURIComponent(threadId)}/history/voice?session=${encodeURIComponent(sessionId)}${cursor ? `&cursor=${cursor}` : ''}`);
  }

  /** Stops and releases a realtime voice session. */
  stopVoiceSession(sessionId: string): Promise<void> {
    return this.sendVoice('DELETE', this.url(`/api/voice/${encodeURIComponent(sessionId)}`));
  }

  /**
   * Opens an SSE stream for voice session events using fetch so the Authorization
   * and X-LooChat-Voice-Owner headers are passed.
   */
  async streamVoiceEvents(sessionId: string, onEvent: (ev: VoiceEvent) => void, signal: AbortSignal): Promise<void> {
    const res = await fetch(this.url(`/api/voice/${encodeURIComponent(sessionId)}/events`), {
      headers: this.voiceHeaders({ Accept: 'text/event-stream' }),
      signal,
    });
    if (res.status === 401) throw await authError(res);
    if (!res.ok || !res.body) throw new Error(`voice stream failed: HTTP ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const data = block
            .split('\n')
            .filter((l) => l.startsWith('data: '))
            .map((l) => l.slice(6))
            .join('\n');
          if (data) {
            try {
              onEvent(JSON.parse(data) as VoiceEvent);
            } catch {}
          }
        }
      }
    } finally {
      void reader.cancel().catch(() => undefined);
    }
  }
}
