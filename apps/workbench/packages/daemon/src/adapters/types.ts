import type { MemoryDocument, MemoryIndex } from '@calli/extension-api';
import type { ImageInput } from '../attachments.js';
import type { EmittedEvent } from '../events.js';
import type { GoalControl, GoalSnapshot } from './native-goal.js';
import type { VoiceContext, VoiceTransport } from '../voice-sessions.js';

/** What the user sent in one turn. `text` may be empty when there are images. */
export interface TurnInput {
  text: string;
  images: ImageInput[];
}

export type WorkMode = 'execute' | 'plan';
export interface UserQuestion {
  id: string;
  question: string;
  header?: string;
  options: Array<{ value: string; label: string; description?: string }>;
  multiple?: boolean;
  freeText?: boolean;
  required?: boolean;
}
export interface UserInputRequest {
  /** Native request identity, used to coalesce reannouncements and cancellations. */
  id?: string;
  title: string;
  kind?: 'question' | 'plan';
  plan?: string;
  questions: UserQuestion[];
  /**
   * Nothing is really being asked: the decision this carries has already been made on the user's
   * behalf by their settings (full access approves a plan gate itself). The request is recorded in
   * the thread like any other — the plan stays readable — but no sheet ever waits for a person.
   */
  auto?: UserInputAnswer;
}
export type UserInputAnswer = { answers: Record<string, string[]> } | { cancelled: true };

export interface PermissionOption {
  optionId: string;
  name: string;
  /** ACP-style kind: allow_once | allow_always | reject_once | reject_always */
  kind: string;
}

export interface ApprovalRequest {
  title: string;
  options: PermissionOption[];
  /** Raw tool-call payload from the agent, shown to the user as-is. */
  toolCall: Record<string, unknown>;
}

export type ApprovalDecision = { optionId: string } | { cancelled: true };

/**
 * Session-level callbacks for turns the agent starts by itself, such as a voice conversation
 * dispatching work. They live for the whole session: a prompted turn finishing never clears them,
 * and they never claim the prompted-turn queue.
 */
export interface SessionHooks {
  /**
   * The agent began a turn nobody prompted. Returns the hooks that turn runs with. `background`
   * marks work the agent took up on its own, such as a finished background task, not speech.
   */
  turnStarted(nativeTurnId: string, opts?: { background?: boolean }): TurnHooks;
  /** That turn ended, in the adapter's own stop-reason vocabulary. */
  turnFinished(nativeTurnId: string, result: { stopReason: string; error?: string }): void;
}

/** Callbacks an adapter uses while a turn is running. */
export interface TurnHooks {
  emit(event: EmittedEvent): void;
  requestApproval(req: ApprovalRequest): Promise<ApprovalDecision>;
  requestInput?(req: UserInputRequest): Promise<UserInputAnswer>;
  cancelInput?(nativeId: string): void;
}

export interface TurnResult {
  stopReason: 'end_turn' | 'cancelled' | 'error' | string;
  error?: string;
  /**
   * Set for errors the UI can explain better than a raw message.
   * `permission_refused`: the agent could not ask for approval, so it refused `refused` and stopped.
   */
  errorCode?: 'permission_refused';
  refused?: string[];
}

/** A live agent session bound to one thread. */
export interface AgentSession {
  /** Stable id on the agent side, used to resume the session after a restart. */
  readonly agentSessionId: string;
  /** Changes the permission level of a live session. Only called when the adapter lists modes. */
  setPermissionMode?(mode: PermissionMode): Promise<void>;
  setWorkMode?(mode: WorkMode): Promise<void>;
  setSpeed?(speed: string | null): Promise<boolean>;
  /** Returns true if the change applied to the live session; false means the session must restart. */
  setModel?(model: string): Promise<boolean>;
  /** Returns true if the change applied to the live session; false means the session must restart. */
  setEffort?(effort: EffortLevel): Promise<boolean>;
  prompt(input: TurnInput, hooks: TurnHooks): Promise<TurnResult>;
  /** Native controls that can run without queueing behind the current goal. */
  controlGoal?(action: GoalControl): Promise<string>;
  /** The native goal right now, for the goal card. Null when there is none. */
  goalState?(): Promise<GoalSnapshot | null>;
  /** Hooks for agent-initiated turns (voice). Set once per session; present only where the adapter implements them. */
  setSessionHooks?(hooks: SessionHooks): void;
  /**
   * A one-use native voice channel on this session's own connection. Null when the session's
   * process is gone: the caller refuses instead of respawning it silently.
   */
  voiceTransport?(context?: VoiceContext): VoiceTransport | null;
  cancel(): Promise<void>;
  close(): Promise<void>;
}

/**
 * Permission levels shared by the UI and the daemon. Each adapter maps these to its own modes.
 * - ask: every action that needs approval asks the user
 * - auto_edit: file edits are approved automatically, other actions still ask
 * - full_access: no approval prompts
 */
export const PERMISSION_MODES = ['ask', 'auto_edit', 'full_access'] as const;
export type PermissionMode = (typeof PERMISSION_MODES)[number];

/** Thinking effort, shared by the UI. Each adapter lists the subset it supports. */
export const EFFORT_LEVELS = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] as const;
export type EffortLevel = (typeof EFFORT_LEVELS)[number];

/** Where an agent looks for skills: a folder of skill folders (each with a SKILL.md). */
export interface SkillDir {
  /** Absolute path, resolved from the instance's own environment. */
  path: string;
  /** `personal` is the agent's own config dir; `shared` is the cross-agent `~/.agents/skills`. */
  scope: 'personal' | 'shared';
}

/**
 * An MCP server as the agent's own config states it. `config` is the server object exactly as it
 * stands there, so it can be shown, copied and reinstalled without the hub understanding every
 * field a given agent adds around `command`/`args`/`env` or `url`.
 */
export interface McpServerInfo {
  name: string;
  config: Record<string, unknown>;
  /** The config file the entry was read from, shown on the page. */
  sourcePath: string;
}

/** A slash command the agent understands, shown when the user types "/" in the composer. */
export interface SlashCommand {
  /** Without the leading slash. */
  name: string;
  description: string;
  /** What to type after the command, as the agent describes it. */
  hint?: string;
  /** Set only when the agent says what the command is. Claude does not; pi does. */
  kind?: 'command' | 'prompt' | 'skill';
}

/** One window of a plan's usage, as the agent itself reports it. */
export interface QuotaWindow {
  /** Short chip label, such as "5H" or "7D". */
  label: string;
  /** What the window covers, when the agent splits by model group or model. */
  scope?: string;
  /** Exact model IDs covered by this scoped window; absent means the scope is unmapped. */
  modelIds?: string[];
  /** How much of the window is used, 0 to 100. */
  usedPercent: number;
  /** When the window resets, if the agent said so. ISO. */
  resetsAt?: string;
  /** How busy the model behind this window is right now; above 100 means overloaded. */
  loadPercent?: number;
}

/** Money left on a pay-as-you-go key, as the vendor states it; never turned into a percentage. */
export interface QuotaBalance {
  /** Whose balance: the provider or account behind it. */
  scope: string;
  /** The amount with its currency, as reported, such as "¥60.52". */
  amount: string;
  /** Exact model IDs billed from this balance; absent means unmapped. */
  modelIds?: string[];
}

/** Plan usage of an agent instance, read outside any session so nothing is left behind. */
export interface PlanQuota {
  windows: QuotaWindow[];
  /** Balances of keys billed per use, next to the plans' windows. */
  balances?: QuotaBalance[];
  /** An adapter's explanation of missing or limited quota data. */
  note?: string;
  /**
   * The subscription the account is on: `id` as the agent reports it, `label` as shown. Absent when
   * the agent does not say; never guessed.
   */
  plan?: { id: string; label: string };
  /** When the agent was last asked. ISO. */
  checkedAt: string;
}

export interface ModelOption {
  /** Value sent back when the user picks this model. */
  id: string;
  label: string;
  /** Short note from the agent, such as the concrete version behind an alias. */
  description?: string;
  /** How busy this model is right now; above 100 means overloaded. */
  loadPercent?: number;
  /** Total queued requests when reported. Null means unavailable, not zero; absent means no queue metadata. */
  queueSize?: number | null;
  /** Observed request position, not total queue size. Zero means observed nonqueued; null means unknown/stale. */
  queuePosition?: number | null;
  /** Queue observation time in epoch milliseconds; absent when no valid observation exists. */
  queueObservedAt?: number;
  /** The upstream load query failed; distinguish a failed read from unreported metadata. */
  loadError?: boolean;
  /**
   * Thinking effort levels this model accepts. An empty list means the model has no effort setting.
   * Left out when the agent does not say, in which case the agent-wide list applies.
   */
  efforts?: EffortLevel[];
  /** Native speed tiers for this model. Missing or empty means no speed control. */
  speeds?: Array<{ id: string; label: string; description?: string; isFast?: boolean }>;
  /** False when the model cannot read images. Left out when the agent does not say. */
  images?: boolean;
  /**
   * For a model that cannot read images, how they reach it anyway, such as a tool that hands them to a
   * vision model. Images are then accepted for this model.
   */
  imagesVia?: string;
  /** The model the agent uses when the thread does not pick one. */
  isDefault?: boolean;
}

/** A conversation the agent has on record, whether or not the hub knows about it. */
export interface AgentSessionSummary {
  id: string;
  cwd: string;
  title: string;
  /** ISO time of the last activity, when the agent says. */
  updatedAt: string | null;
}

/** The whole prompt a conversation runs with, as the agent itself persisted it. */
export interface SystemPrompt {
  text: string;
  /** What was read, for a caption: the agent names its own shape ("Claude Code 会话文件"). */
  source: string;
}

/** One past exchange, in the shape a live turn would have produced. */
export interface HistoryTurn {
  text: string;
  images: ImageInput[];
  events: EmittedEvent[];
  /**
   * What forkSession needs to copy the conversation as it stood when this turn ended. Only from
   * adapters with capabilities.forkAt, and missing where the agent cannot stop a copy: an adapter
   * may leave it off the last turn, whose copy is the whole conversation anyway.
   */
  forkRef?: string;
  /**
   * What copying a conversation needs to stop *before* this turn: the new session holds what came
   * before it and none of it. Only where the agent can name that boundary (a user message it can
   * fork from, or a turn it can fork before); first turns have no earlier turn to borrow from.
   */
  forkBeforeRef?: string;
}

/** An account of an adapter that is already signed in on this host, ready to become an instance. */
export interface DiscoveredAccount {
  /** The account's login, as the agent itself reports it, to tell accounts apart. */
  email: string;
  /** Instance env that makes an adapter use this account. Built by the adapter, opaque to the UI. */
  env: Record<string, string>;
  /** The account an instance without its own env would use; the registry skips it when one exists. */
  isDefault?: boolean;
}

export interface StartOptions {
  /** Internal metadata probes must not take the shortcuts used to create a real conversation. */
  probe?: boolean;
  /** Native capability requested for an isolated LoopX tool job, absent on ordinary threads. */
  borrowedTool?: string;
  workMode?: WorkMode;
  onWorkMode?: (mode: WorkMode) => void;
  onSpeed?: (speed: string) => void;
  /** The native goal changed, or was cleared with null. */
  onGoal?: (goal: GoalSnapshot | null) => void;
  /** Ephemeral loochat connection context. Never persisted in agent configuration. */
  coordinationEnv?: Record<string, string>;
  cwd: string;
  /** Agent-side session id to resume, if the thread already has one. */
  resumeId?: string;
  /** A lazily created native conversation announced its id during a real prompt. */
  onSessionId?: (id: string) => void;
  /** Permission level for this session. Adapters without permission modes ignore it. */
  permissionMode?: PermissionMode;
  model?: string;
  effort?: EffortLevel;
  speed?: string | null;
}

export interface MemoryReader {
  /** Canonical identity used only inside the daemon to merge shared stores. */
  root(): Promise<string>;
  list(): Promise<MemoryIndex>;
  read(id: string): Promise<MemoryDocument>;
}

/** Compatibility information for an opt-in bridge that patches a private runtime copy. */
export interface StreamBridgeCapability {
  platform: string;
  temporaryMegabytes: number;
}

export interface AgentAdapter {
  /** Optional task capabilities exposed through LoopX's on-demand CLI. */
  readonly borrowedTools?: readonly import('./borrowed-tools.js').BorrowedTool[];
  readonly id: string;
  readonly label: string;
  /** The agent's mark: the path of a 24×24 drawing, filled with the even-odd rule. See icons.ts. */
  readonly icon?: string;
  /** Command or guide to install this agent when it is not yet on PATH. */
  readonly installGuide?: string;
  /** The session emits turn.phase(prompt_sent) itself, at the moment the prompt really leaves (after
   *  any preparation inside prompt). Otherwise the manager emits it just before calling prompt. */
  readonly reportsPromptSent?: boolean;
  readonly capabilities: {
    /** The adapter can inspect existing generated memory; this does not imply generation is enabled. */
    memory?: boolean;
    workModes?: readonly WorkMode[];
    speed?: boolean;
    /** Accepts runtime-only loochat CLI connection context. */
    coordination?: boolean;
    /** Changing model preserves the native conversation, including through a resumed process. */
    modelSwitch?: boolean;
    /** False when the native CLI has no model catalog; keep its defaults without a picker. */
    modelCatalog?: boolean;
    /** User-controlled experimental streaming bridge, not a native protocol capability. */
    streamBridge?: StreamBridgeCapability;
    resume: boolean;
    approvals: boolean;
    /** Permission levels this agent supports. Empty means the UI shows no permission picker. */
    permissionModes: readonly PermissionMode[];
    /** The level a new thread starts at when none is chosen. Without one, `auto_edit`. */
    defaultPermissionMode?: PermissionMode;
    /** Thinking effort levels this agent supports. Empty means the UI shows no effort picker. */
    effortLevels: readonly EffortLevel[];
    /** Whether a turn may carry images. A model can still opt out through ModelOption.images. */
    images: boolean;
    /** Whether the agent's own copy of a session can be removed when a thread is deleted for good. */
    deleteSession: boolean;
    /** Whether existing conversations can be listed and their history read, for importing. */
    history: boolean;
    /** Whether plan usage can be read outside a session, for the quota panel. */
    quota: boolean;
    /** Whether /compact can compress the current context. */
    compact?: boolean;
    /** Native goal controls supported while a turn is running. */
    goal?: { controls: readonly GoalControl[]; actions?: readonly ('set' | 'show' | 'pause' | 'resume' | 'clear')[]; /** The hub can follow the goal's state live, so the card replaces the menu commands. */
      live?: boolean };
    /** Whether a conversation can be copied into a new, independent one. */
    fork: boolean;
    /** Whether that copy can stop at an earlier turn instead of taking everything. Needs fork and history. */
    forkAt: boolean;
    /**
     * Whether an earlier user message can be edited and sent again, by copying the conversation up
     * to just before it. Needs forkAt; a turn whose boundary the agent cannot name is refused then.
     */
    editMessage?: boolean;
    /** Whether more accounts of this agent can be linked, each as its own instance with its own login. */
    multiAccount: boolean;
    /** Can create an independent account directory from this instance's configuration. */
    createAccount?: boolean;
    /** Whether that copy can go on in another directory, a new worktree of the same repository. Needs fork. */
    forkInto: boolean;
    /** Whether the whole prompt the model runs with can be read from the agent's session file. */
    systemPrompt?: boolean;
    /**
     * Whether the adapter implements native realtime voice sessions on a live session
     * (`voiceTransport`). This says the code exists; login and protocol support are probed live
     * per install (`realtimeSupport`) and per session start, never assumed from the name.
     */
    realtimeVoice?: boolean;
  };
  /** Effective setting, including this instance's environment and an inherited hub default. */
  readonly streamBridgeEnabled?: boolean;
  /**
   * Save the adapter-owned setting environment. Enabling never fails on an incompatible
   * installation; `incompatible` explains why turns will run without the bridge instead.
   */
  streamBridgeEnvironment?(enabled: boolean): Promise<{ environment: Record<string, string>; incompatible?: string }>;
  memoryReader?(): MemoryReader;
  /** Whether the agent can be started on this host. */
  available(): Promise<boolean>;
  /** Read-only protocol probe; account authorization is checked again when voice starts. */
  realtimeSupport?(): Promise<{ supported: boolean; reason?: string; login?: 'ready' | 'required' }>;
  /** Models the user can pick from. May start a short-lived process to ask the agent. */
  models(refresh?: boolean): Promise<ModelOption[]>;
  /** A background catalog or capability probe changed the last published list. */
  onModelsChanged?: (models: ModelOption[]) => void;
  /** Plan usage observed in-band as the agent answers (Claude Code's rate_limit_event): free and
   *  fresher than any probe, so it feeds the between-turn quota decisions and the cache it reads. */
  onQuotaObserved?: (quota: PlanQuota) => void;
  /** Resolve deferred image capabilities before accepting an image attachment. */
  modelForImages?(model: string | null): Promise<ModelOption | undefined>;
  /** Read-only runtime identity; no prompt, login or config write. */
  diagnose?(): Promise<import('./diagnostics.js').RuntimeDiagnostic>;
  /**
   * Slash commands the agent reports. Project-level commands depend on the directory, so pass the
   * thread's cwd when there is one. May be empty until the agent has announced them.
   */
  commands(cwd?: string): Promise<SlashCommand[]>;
  /** The command that continues this session in a terminal, to be run from the thread's directory. */
  resumeCommand(agentSessionId: string): string;
  /**
   * Whether a failed turn's message says the account's plan has run out (a session or usage limit),
   * so the hub can offer another agent to take over. Agents word this differently; each says its own.
   */
  planExhausted?(message: string): boolean;
  /** Read-only location of this instance's native session, without starting an agent. */
  sessionLocation?(agentSessionId: string | null, cwd: string): Promise<{
    configDir: string;
    sessionPath: string | null;
    /** Account-scoped command, to run in the thread's working directory. */
    resumeCommand?: string;
  }>;
  /**
   * Copies a session file of another instance of the same agent (another account) into this
   * instance's own store, under a new id, and returns that id. The copy resumes here with the whole
   * conversation; the original is left as it was. Agents that cannot resume a conversation
   * another account wrote leave this out.
   */
  importSession?(sourcePath: string): Promise<string>;
  /** Removes the agent's own record of a session. Present only when capabilities.deleteSession is true. */
  deleteSession?(agentSessionId: string, cwd: string): Promise<void>;
  /**
   * The whole system prompt of one conversation, read from the agent's own session file without
   * starting anything. With capabilities.systemPrompt. Null when there is no session yet or the
   * file holds no prompt; never throws for a missing file.
   */
  readSystemPrompt?(agentSessionId: string, cwd: string): Promise<SystemPrompt | null>;
  /**
   * Which front end started a native session, from the session file's first line: `app` for the
   * agent's desktop app, `cli` for its terminal client, null when the record does not say.
   */
  sessionOrigin?(firstLine: string): 'app' | 'cli' | null;
  /** Every conversation the agent has on record, in any directory. With capabilities.history. */
  listSessions?(): Promise<AgentSessionSummary[]>;
  /** Read one session's metadata without listing every session or loading its turns. */
  getSession?(agentSessionId: string): Promise<AgentSessionSummary | null>;
  /** The past turns of a conversation. Reading must not change the conversation. With capabilities.history. */
  loadHistory?(agentSessionId: string, cwd: string): Promise<HistoryTurn[]>;
  /**
   * Folders outside the thread's directory where the agent keeps files it made for this session.
   * Images in them may be shown in the thread, like images in the thread's own directory.
   */
  artifactDirs?(agentSessionId: string): string[];

  /**
   * Where this instance looks for skills, read from the filesystem without starting the agent.
   * The first `personal` dir is where a synced skill is written for this instance. Agents whose
   * skill locations are not known leave this out; the page then shows no badge for them.
   */
  skillDirs?(): SkillDir[];

  /**
   * The MCP servers this instance is configured with, read from the agent's own config without
   * starting anything. Agents whose MCP config is not known leave this out and simply do not
   * appear on the MCP page. `mcpInstall`/`mcpRemove` are present only where the config can also
   * be written back safely, one entry at a time.
   */
  mcpServers?(): McpServerInfo[];
  mcpInstall?(name: string, config: Record<string, unknown>): void;
  mcpRemove?(name: string): void;

  /** Plan usage, read outside any session. With capabilities.quota. Cached one layer up. */
  fetchQuota?(): Promise<PlanQuota>;
  /**
   * Accounts of this agent already signed in on this host, whatever directories they live in. With
   * capabilities.multiAccount. Never throws: what cannot be read is simply not offered.
   */
  discoverAccounts?(home: string): Promise<DiscoveredAccount[]>;
  /**
   * A shell command that starts this instance's login in a terminal, with the instance's own
   * account environment. Omitted where the CLI has no login step the hub can start.
   */
  loginCommand?(): string;
  createAccount?(directory: string): Promise<Record<string, string>>;
  startLogin?(onSuccess: (account?: string) => Promise<void>): LoginSession;
  /**
   * Copies a conversation and returns the id of the copy. With capabilities.fork. Given a turn's
   * forkRef (with capabilities.forkAt), the copy ends where that turn ended. Given `intoCwd` (with
   * capabilities.forkInto), the copy is one that can be started there instead of in `cwd`.
   */
  forkSession?(agentSessionId: string, cwd: string, forkRef?: string, intoCwd?: string): Promise<string>;
  /**
   * Copies a conversation up to, but not including, the turn `forkBeforeRef` names, and returns the
   * id of the copy. With capabilities.editMessage, for turns the previous turn's forkRef cannot
   * express — the first turn above all.
   */
  forkBeforeSession?(agentSessionId: string, cwd: string, forkBeforeRef: string): Promise<string>;
  start(opts: StartOptions): Promise<AgentSession>;
}

export interface LoginState {
  status: 'starting' | 'waiting' | 'submitting' | 'success' | 'error' | 'cancelled';
  url?: string;
  error?: 'failed' | 'expired' | 'unavailable' | 'verification';
}

export interface LoginSession {
  state(): LoginState;
  submit(code: string): void;
  cancel(): void;
}

/** Preserve account environment and its PATH while exposing the runtime CLI. */
export function sessionEnvironment(base: Record<string, string>, context?: Record<string, string>): NodeJS.ProcessEnv {
  // A daemon launched from another agent must never lend that agent's scoped credentials
  // to a new session. Undefined also masks inherited keys in adapters that merge env again.
  const env: NodeJS.ProcessEnv = {
    ...process.env, ...base, LOOCHAT_TEAM_SOCKET: undefined, LOOCHAT_TEAM_KEY: undefined, LOOCHAT_BIN_DIR: undefined,
    LOOCHAT_HANDOVER_SOCKET: undefined, LOOCHAT_HANDOVER_KEY: undefined,
    LOOCHAT_TODO_SOCKET: undefined, LOOCHAT_TODO_KEY: undefined,
    LOOCHAT_TOOLS_SOCKET: undefined, LOOCHAT_TOOLS_KEY: undefined, LOOCHAT_TOKEN: undefined, ...context,
  };
  if (context?.LOOCHAT_BIN_DIR) env.PATH = `${context.LOOCHAT_BIN_DIR}${process.platform === 'win32' ? ';' : ':'}${base.PATH ?? process.env.PATH ?? ''}`;
  return env;
}

/**
 * Names the session store an instance's conversations live in: instances of one adapter whose
 * store-picking env matches (or sets none) share their sessions, so the local-session scan asks
 * one of them and a session never appears under two instances. Which env picks the store is an
 * agent difference, so it lives here with the adapters.
 */
export function sessionStoreKey(instance: { adapter: string; env?: Record<string, string> }): string {
  switch (instance.adapter) {
    case 'claude':
      return `claude:${instance.env?.CLAUDE_CONFIG_DIR ?? ''}`;
    case 'codex':
      return `codex:${instance.env?.CODEX_HOME ?? ''}`;
    case 'traex':
      return `traex:${instance.env?.LOOCHAT_TRAEX_HOME ?? instance.env?.CODEX_HOME ?? ''}`;
    default:
      return instance.adapter;
  }
}
