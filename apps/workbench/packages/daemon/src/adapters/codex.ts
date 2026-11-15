import { CodexVoiceTransport, peerTransport } from './codex-realtime.js';
import type { VoiceContext, VoiceTransport } from '../voice-sessions.js';
import type { SessionHooks } from './types.js';
import { nativeQuestions } from './questions.js';
import type { WorkMode } from './types.js';
import { CodexUsage } from './usage.js';
import { cliDiagnostic } from './diagnostics.js';
import { trackCompaction } from './compaction.js';
import { goalSnapshot, goalSummary, parseGoalCommand, type GoalControl, type GoalCommand, type GoalSnapshot } from './native-goal.js';
import { sessionEnvironment } from './types.js';
import { codexReadTool, codexSearchTool } from './codex-tools.js';
import { codexComputerTool, computerDynamicTool } from './codex-computer.js';
import { BrowserComputer } from '../browser-computer.js';
import { spawn } from 'node:child_process';
import { CodexMemoryReader } from './codex-memory.js';
import { homedir, tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import type { EmittedEvent } from '../events.js';
import { commandExists } from '../util.js';
import { titlePlan } from './plans.js';
import { CODEX_ICON } from './icons.js';
import { configDirectory, findSessionFile, quoteArg } from './session-location.js';
import { codexSystemPromptMeta, scanJsonl } from './system-prompt.js';
import { installTomlMcpServer, removeTomlMcpServer, tomlMcpServers } from '../mcp-format.js';
import { JsonRpcPeer, RpcError } from './jsonrpc.js';
import type {
  AgentAdapter,
  AgentSession,
  AgentSessionSummary,
  ApprovalDecision,
  ApprovalRequest,
  EffortLevel,
  HistoryTurn,
  McpServerInfo,
  ModelOption,
  PermissionMode,
  PermissionOption,
  PlanQuota,
  SkillDir,
  SlashCommand,
  StartOptions,
  SystemPrompt,
  TurnHooks,
  TurnInput,
  TurnResult,
} from './types.js';

/**
 * Codex, through its own `codex app-server` JSON-RPC protocol over stdio. The shape of a turn:
 * `thread/start` (or `thread/resume`) once, then `turn/start` per message, which answers at once
 * while the work streams in as notifications — items that start and complete (commands, file
 * changes, MCP calls), deltas for the agent's words and its reasoning summaries, and a
 * `turn/completed` when everything is over. Approvals arrive as JSON-RPC requests from the server.
 */

type ApprovalsReviewer = 'user' | 'auto_review';

/** One hub permission level, as the sandbox, approval policy and native reviewer Codex exposes. */
const CODEX_MODES: Record<PermissionMode, { sandbox: string; approvalPolicy: string; approvalsReviewer: ApprovalsReviewer }> = {
  // Codex's own three modes: Read Only, Auto, Full Access.
  ask: { sandbox: 'read-only', approvalPolicy: 'on-request', approvalsReviewer: 'user' },
  auto_edit: { sandbox: 'workspace-write', approvalPolicy: 'on-request', approvalsReviewer: 'auto_review' },
  full_access: { sandbox: 'danger-full-access', approvalPolicy: 'never', approvalsReviewer: 'user' },
};

/** Metadata shared by the index and a single-thread read; never hydrate turns for a title. */
function codexSessionSummary(t: any): AgentSessionSummary {
  const title = [t?.name, t?.preview].find((s: unknown): s is string => typeof s === 'string' && s.trim().length > 0);
  return {
    id: String(t?.id ?? ''),
    cwd: String(t?.cwd ?? ''),
    title: (title ?? '').slice(0, 80),
    updatedAt: typeof t?.updatedAt === 'number' ? new Date(t.updatedAt * 1000).toISOString() : null,
  };
}

/** The sandbox names `turn/start` takes, unlike the kebab-case ones `thread/start` wants. */
const SANDBOX_POLICY: Record<string, { type: string }> = {
  'read-only': { type: 'readOnly' },
  'workspace-write': { type: 'workspaceWrite' },
  'danger-full-access': { type: 'dangerFullAccess' },
};

/** Effort levels a Codex model may report that the hub's picker can also offer. */
const KNOWN_EFFORTS = new Set(['minimal', 'low', 'medium', 'high', 'xhigh', 'max']);
const SESSION_SOURCES = ['cli', 'vscode', 'exec', 'appServer', 'unknown'];

/** What `turn/completed` says, mapped to the hub's stop reasons. */
function stopReasonOf(status: string): string {
  if (status === 'interrupted') return 'cancelled';
  if (status === 'failed') return 'error';
  return 'end_turn';
}

/**
 * Turns a unified diff, which is how Codex reports file updates, into the old and new text of the
 * changed region. Context lines belong to both sides; the file's untouched parts are not in the
 * diff, but the hub only shows the region, so the lines it has are all it needs.
 *
 * An added or deleted file is not a patch at all: Codex sends the file's plain content. That is
 * told apart by no line carrying a diff marker.
 */
export function parseUnifiedDiff(diff: string, kind?: string): { oldText: string | null; newText: string } {
  const text = String(diff ?? '');
  const looksLikePatch = /^(@@|--- |\+\+\+ |[-+ \\])/m.test(text);
  // An added or deleted file is not a patch at all: Codex sends the file's plain content, which
  // no line of carries a diff marker. An update always comes as a real patch.
  if ((kind === 'add' || kind === 'delete') && !looksLikePatch) {
    return kind === 'delete' ? { oldText: text, newText: '' } : { oldText: null, newText: text };
  }
  const lines = text.split('\n');
  // A trailing newline ends the last line; it is not an extra empty line.
  if (lines[lines.length - 1] === '') lines.pop();
  const oldLines: string[] = [];
  const newLines: string[] = [];
  for (const line of lines) {
    // File headers (@@, ---, +++) and markers ("\ No newline") are not content.
    if (line.startsWith('@@') || line.startsWith('--- ') || line.startsWith('+++ ') || line.startsWith('\\')) continue;
    if (line.startsWith('-')) oldLines.push(line.slice(1));
    else if (line.startsWith('+')) newLines.push(line.slice(1));
    else if (line.startsWith(' ')) {
      oldLines.push(line.slice(1));
      newLines.push(line.slice(1));
    }
  }
  // No old lines at all means the file is new; the hub reads that as oldText null.
  return { oldText: oldLines.length === 0 && newLines.length > 0 ? null : oldLines.join('\n'), newText: newLines.join('\n') };
}

/** A Codex file change, as the hub's diff cards expect it. */
export function codexDiff(change: any): { type: 'diff'; path: string; oldText: string | null; newText: string } | null {
  if (typeof change?.path !== 'string' || typeof change?.diff !== 'string') return null;
  const { oldText, newText } = parseUnifiedDiff(change.diff, change?.kind?.type);
  return { type: 'diff', path: change.path, oldText, newText };
}

function fileChangeTitle(item: any): string {
  const paths = (item?.changes ?? []).map((c: any) => String(c?.path ?? '')).filter(Boolean);
  return paths.slice(0, 3).join(', ') + (paths.length > 3 ? ` 等 ${paths.length} 个文件` : '');
}

/**
 * One `item/started`, as a tool.start. Returns null for items the UI does not show as a step:
 * the user's own message, agent messages and reasoning (their deltas carry the text), and the
 * internal bookkeeping items.
 */
export function mapCodexItemStart(item: any): EmittedEvent | null {
  switch (item?.type) {
    case 'contextCompaction':
      return { type: 'context.compaction', data: { id: item.id, status: 'in_progress' } };
    case 'commandExecution':
      return {
        type: 'tool.start',
        data: {
          id: item.id,
          title: String(item.command ?? '').split('\n')[0],
          kind: 'execute',
          status: 'in_progress',
          input: { command: item.command, cwd: item.cwd },
        },
      };
    case 'fileChange': {
      const diffs = (item.changes ?? []).map(codexDiff).filter(Boolean);
      return {
        type: 'tool.start',
        data: {
          id: item.id,
          title: fileChangeTitle(item),
          kind: 'edit',
          status: 'in_progress',
          input: { changes: (item.changes ?? []).map((c: any) => ({ path: c?.path, kind: c?.kind?.type })) },
          ...(diffs.length ? { content: diffs } : {}),
        },
      };
    }
    case 'mcpToolCall':
      return {
        type: 'tool.start',
        data: {
          id: item.id,
          title: `${item.server ?? 'mcp'} · ${item.tool ?? ''}`,
          kind: 'other',
          status: 'in_progress',
          input: item.arguments,
          // Kept apart for the activity page: the MCP server and the tool the model called.
          ...(item.tool ? { tool: String(item.tool) } : {}),
          ...(item.server ? { server: String(item.server) } : {}),
        },
      };
    case 'webSearch':
      return { type: 'tool.start', data: { id: item.id, title: String(item.query ?? ''), kind: 'search', status: 'in_progress',
        tool: 'web_search', ...(item.action ? { action: item.action } : {}) } };
    case 'imageGeneration':
      return { type: 'tool.start', data: { id: item.id, title: '图片', kind: 'create', status: 'in_progress', input: { revisedPrompt: item.revisedPrompt } } };
    case 'dynamicToolCall':
      return {
        type: 'tool.start',
        data: {
          id: item.id,
          title: `${item.namespace ?? ''} · ${item.tool ?? ''}`,
          kind: 'other',
          status: 'in_progress',
          input: item.arguments,
          ...(item.tool ? { tool: [item.namespace, item.tool].filter(Boolean).map(String).join('·') } : {}),
        },
      };
    default:
      return null;
  }
}

/** One `item/completed`, including native compaction boundaries. */
export function mapCodexItemEnd(item: any): EmittedEvent | null {
  const base = { id: item?.id };
  switch (item?.type) {
    case 'contextCompaction':
      return { type: 'context.compaction', data: { ...base, status: 'completed' } };
    case 'commandExecution': {
      const failed = item.status !== 'completed' || (typeof item.exitCode === 'number' && item.exitCode !== 0);
      const output = [item.aggregatedOutput, failed && typeof item.exitCode === 'number' ? `(exit ${item.exitCode})` : null]
        .filter((s): s is string => typeof s === 'string' && s.length > 0)
        .join('\n');
      return { type: 'tool.end', data: { ...base, status: failed ? 'failed' : 'completed', ...(output ? { output } : {}) } };
    }
    case 'fileChange': {
      const diffs = (item.changes ?? []).map(codexDiff).filter(Boolean);
      return {
        type: 'tool.end',
        data: { ...base, status: item.status === 'failed' ? 'failed' : 'completed', ...(diffs.length ? { content: diffs } : {}) },
      };
    }
    case 'mcpToolCall': {
      const failed = item.status !== 'completed' || item.error;
      const output = item.error?.message ?? item.error?.toString?.() ?? null;
      return { type: 'tool.end', data: { ...base, status: failed ? 'failed' : 'completed', ...(output ? { output: String(output) } : {}) } };
    }
    case 'webSearch': {
      // Persist the bounded citation metadata, not opaque search bodies or provider media.
      const results = (Array.isArray(item.results) ? item.results : []).filter((r: any) => typeof r?.url === 'string')
        .slice(0, 24).map((r: any) => ({ url: r.url.slice(0, 4000), title: typeof r.title === 'string' ? r.title.slice(0, 300) : '' }));
      const output = results.map((r: any) => r?.title).filter(Boolean).join('\n');
      return { type: 'tool.end', data: { ...base, status: 'completed', tool: 'web_search',
        ...(item.action ? { action: item.action } : {}), ...(results.length ? { results } : {}), ...(output ? { output } : {}) } };
    }
    case 'imageGeneration':
      return { type: 'tool.end', data: { ...base, status: item.failure ? 'failed' : 'completed', output: item.savedPath ?? item.failure?.message ?? undefined } };
    case 'dynamicToolCall':
      return { type: 'tool.end', data: { ...base, status: item.status === 'failed' || !item.success ? 'failed' : 'completed' } };
    default:
      return null;
  }
}

/** Codex's plan steps, as the hub's task cards expect them. */
export function planEntries(steps: any): Array<{ content: string; status: string }> {
  return (Array.isArray(steps) ? steps : [])
    .filter((s: any) => typeof s?.step === 'string' && s.step.trim())
    .map((s: any) => ({
      content: String(s.step),
      status: s.status === 'inProgress' ? 'in_progress' : s.status === 'completed' ? 'completed' : 'pending',
    }));
}

/** Only advertise catalog tiers; legacy fast is the API's priority service tier. */
export function codexSpeedOptions(model: any): NonNullable<ModelOption['speeds']> {
  const raw = Array.isArray(model.serviceTiers) ? model.serviceTiers
    : (Array.isArray(model.additionalSpeedTiers) ? model.additionalSpeedTiers : []).map((id: string) => ({ id: id === 'fast' ? 'priority' : id, name: id }));
  const names: Record<string, string> = { default: '标准', priority: '快速', fast: '快速', ultrafast: '超快', flex: '经济' };
  const tiers = raw.filter((t: any) => typeof t?.id === 'string' && t.id && t.id !== 'default');
  if (!tiers.length) return [];
  return [{ id: 'default', label: '标准' }, ...tiers.map((t: any) => ({ id: t.id, label: names[t.id] ?? t.name ?? t.id,
    isFast: ['priority', 'fast', 'ultrafast'].includes(t.id),
    description: ['priority', 'fast', 'ultrafast'].includes(t.id) ? '速度更快，用量可能增加' : t.description || undefined,
  }))].filter((t, i, all) => all.findIndex(v => v.id === t.id) === i);
}

/** The model list as `model/list` reports it, in the hub's shape. */
export function codexModelOptions(models: any[]): ModelOption[] {
  return (models ?? [])
    .filter((m) => typeof m?.id === 'string' && !m?.hidden)
    .map((m): ModelOption => {
      const efforts = (m.supportedReasoningEfforts ?? [])
        .map((e: any) => e?.reasoningEffort ?? e)
        .filter((e: unknown): e is EffortLevel => typeof e === 'string' && KNOWN_EFFORTS.has(e));
      return {
        id: m.id,
        label: m.displayName || m.model || m.id,
        ...(typeof m.description === 'string' && m.description ? { description: m.description } : {}),
        ...(Array.isArray(m.supportedReasoningEfforts) ? { efforts } : {}),
        ...(Array.isArray(m.inputModalities) ? { images: m.inputModalities.includes('image') } : {}),
        ...(m.isDefault ? { isDefault: true } : {}),
        ...(codexSpeedOptions(m).length ? { speeds: codexSpeedOptions(m) } : {}),
      };
    });
}

/**
 * Builds the hub's approval choices from the decisions Codex says it would take. "cancel" is left
 * out on purpose: cancelling the approval (or the turn) already answers it that way.
 */
export function codexApprovalOptions(available: unknown): PermissionOption[] {
  const out: PermissionOption[] = [];
  const has = (id: string) => (Array.isArray(available) ? available.some((d) => d === id || (d && typeof d === 'object' && id in (d as object))) : false);
  if (!Array.isArray(available) || has('accept')) out.push({ optionId: 'accept', name: '允许这一次', kind: 'allow_once' });
  if (has('acceptForSession')) out.push({ optionId: 'acceptForSession', name: '本会话内不再询问', kind: 'allow_always' });
  if (has('acceptWithExecpolicyAmendment')) out.push({ optionId: 'acceptWithExecpolicyAmendment', name: '允许，同类命令不再询问', kind: 'allow_always' });
  if (has('decline')) out.push({ optionId: 'decline', name: '拒绝', kind: 'reject_once' });
  return out;
}

/** The answer to send back for a hub decision, in Codex's approval-decision shape. */
export function codexApprovalDecision(decision: ApprovalDecision, params: any): { decision: unknown } {
  if ('cancelled' in decision) return { decision: 'cancel' };
  switch (decision.optionId) {
    case 'accept':
      return { decision: 'accept' };
    case 'acceptForSession':
      return { decision: 'acceptForSession' };
    case 'acceptWithExecpolicyAmendment':
      return { decision: { acceptWithExecpolicyAmendment: { execpolicy_amendment: params?.proposedExecpolicyAmendment ?? [] } } };
    default:
      return { decision: 'decline' };
  }
}

/**
 * Past turns of a conversation, as `thread/turns/list` reports them, in the shape a live turn
 * would have produced. The user message opens a turn; the other items become steps and text.
 */
export function turnsFromCodexHistory(turns: any[]): HistoryTurn[] {
  const out: HistoryTurn[] = [];
  for (const turn of turns ?? []) {
    // Codex forks "through" a turn it is given the id of, and before a turn it is given instead.
    const current: HistoryTurn = { text: '', images: [], events: [], ...(typeof turn?.id === 'string' && turn.id ? { forkRef: turn.id, forkBeforeRef: turn.id } : {}) };
    for (const item of turn?.items ?? []) {
      if (item?.type === 'userMessage') {
        current.text = (item.content ?? [])
          .filter((c: any) => c?.type === 'text')
          .map((c: any) => String(c.text ?? ''))
          .join('');
        continue;
      }
      if (item?.type === 'agentMessage' || item?.type === 'exitedReviewMode') {
        const text = item.type === 'exitedReviewMode' ? item.review : item.text;
        if (text) current.events.push({ type: 'message.delta', data: { text: String(text) } });
        continue;
      }
      if (item?.type === 'reasoning') {
        const text = [...(item.summary ?? []), ...(item.content ?? [])]
          .map((b: any) => (typeof b?.text === 'string' ? b.text : ''))
          .join('')
          .trim();
        if (text) current.events.push({ type: 'reasoning.delta', data: { text } });
        continue;
      }
      if (item?.type === 'plan') {
        current.events.push({ type: 'plan.completed', data: { id: item.id, text: item.text ?? '' } });
        continue;
      }
      const start = mapCodexItemStart(item);
      const end = mapCodexItemEnd(item);
      if (start) current.events.push(start);
      if (end) current.events.push(end);
    }
    if (current.text || current.events.length) out.push(current);
  }
  return out;
}

/**
 * Commands the adapter answers itself, the way the pi adapter does. Codex's other slash commands
 * are features of its terminal UI, not of app-server; the hub offers its own panels for those.
 */
export const CODEX_LOCAL_COMMANDS: SlashCommand[] = [
  { name: 'goal', description: '设置原生目标并持续执行；可查看、暂停、继续或清除', hint: '[目标 | pause | resume | clear]', kind: 'command' },
  { name: 'compact', description: '压缩当前线程的上下文', kind: 'command' },
  { name: 'review', description: '审查未提交的修改，或按说明审查代码', hint: '[审查说明]', kind: 'command' },
  { name: 'skills', description: '查看当前目录可用的技能', kind: 'command' },
  { name: 'mcp', description: '查看 MCP 服务、工具数量和认证状态', kind: 'command' },
  { name: 'usage', description: '查看套餐额度和重置时间', kind: 'command' },
];

/**
 * ChatGPT plans as Codex reports them. `prolite` is the $100 Pro plan (5x Plus's limits) and `pro`
 * the $200 one (20x); OpenAI names both "Pro", so the multiple tells them apart, as Claude's Max does.
 */
const CODEX_PLANS: Record<string, string> = {
  free: 'Free', go: 'Go', plus: 'Plus', prolite: 'Pro 5x', pro: 'Pro 20x', team: 'Team', business: 'Business', enterprise: 'Enterprise', edu: 'Edu',
};

export function codexPlan(raw: unknown): PlanQuota['plan'] {
  if (typeof raw !== 'string' || !raw.trim()) return undefined;
  const id = raw.trim();
  return { id, label: CODEX_PLANS[id.toLowerCase()] ?? titlePlan(id) };
}

/** Prefer the complete bucket map; older servers only return the single snapshot. */
export function codexQuota(response: any): PlanQuota {
  const buckets = Object.entries(response?.rateLimitsByLimitId ?? {});
  if (!buckets.length && response?.rateLimits) buckets.push([response.rateLimits.limitId ?? 'codex', response.rateLimits]);
  const windows: PlanQuota['windows'] = [];
  for (const [id, raw] of buckets) {
    const bucket = raw as any;
    for (const [key, fallback] of [['primary', '主要'], ['secondary', '次要']]) {
      const window = bucket?.[key];
      if (typeof window?.usedPercent !== 'number' || !Number.isFinite(window.usedPercent)) continue;
      const mins = window.windowDurationMins;
      const label = typeof mins !== 'number' || !Number.isFinite(mins) || mins <= 0 ? fallback
        : mins % 1440 === 0 ? `${mins / 1440}D` : mins % 60 === 0 ? `${mins / 60}H` : `${mins}M`;
      const reset = typeof window.resetsAt === 'number' ? new Date(window.resetsAt * 1000) : null;
      windows.push({
        label,
        ...(buckets.length > 1 || bucket?.limitName || id !== 'codex' ? { scope: bucket?.limitName || id } : {}),
        usedPercent: Math.max(0, Math.min(100, window.usedPercent)),
        ...(reset && Number.isFinite(reset.getTime()) ? { resetsAt: reset.toISOString() } : {}),
      });
    }
  }
  const weeklyOnly = windows.length > 0 && windows.every((window) => window.label === '7D');
  // The plan rides along with the limits: no second request.
  const plan = codexPlan(response?.rateLimits?.planType ?? buckets.map(([, b]) => (b as any)?.planType).find((p) => typeof p === 'string'));
  return {
    windows,
    ...(plan ? { plan } : {}),
    ...(weeklyOnly ? { note: '本次仅返回每周额度，服务端未提供 5 小时窗口。' } : {}),
    checkedAt: new Date().toISOString(),
  };
}

interface CodexSkill { name: string; description: string; path: string }

async function readSkills(peer: JsonRpcPeer, cwd: string): Promise<CodexSkill[]> {
  const res = await peer.request('skills/list', { cwds: [cwd] });
  const skills = new Map<string, CodexSkill>();
  for (const entry of res?.data ?? []) {
    for (const skill of entry?.skills ?? []) {
      if (skill?.enabled === false || typeof skill?.name !== 'string' || !skill.name || typeof skill.path !== 'string') continue;
      if (CODEX_LOCAL_COMMANDS.some((command) => command.name === skill.name)) continue;
      if (!skills.has(skill.name)) skills.set(skill.name, {
        name: skill.name,
        description: skill.interface?.shortDescription || skill.shortDescription || skill.description || '使用此技能',
        path: skill.path,
      });
    }
  }
  return [...skills.values()];
}

const MAX_PENDING_NOTIFICATIONS = 200;
const MAX_FINISHED_TURNS = 200;

/** A live Codex thread: one process, one thread id, any number of turns. */
class CodexSession implements AgentSession {
  private readonly accounting = new CodexUsage();
  private hooks: TurnHooks | null = null;
  private sessionHooks?: SessionHooks;
  private voice?: CodexVoiceTransport;
  private voiceReusable = true;
  private voiceStarted = false;

  setSessionHooks(hooks: SessionHooks): void { this.sessionHooks = hooks; }

  voiceTransport(context?: VoiceContext): VoiceTransport | null {
    if (this.peer.isClosed || !this.voiceReusable) return null;
    this.voiceReusable = false;
    this.voiceStarted = false;
    const transport = new CodexVoiceTransport(this.agentSessionId, peerTransport(this.peer), { closeTimeoutMs: 2000 }, context);
    this.voice = transport;
    return {
      start: async (offer, emit) => {
        this.voiceStarted = true;
        try { await transport.start(offer, emit); }
        catch (error) {
          // Authentication failed before realtime/start; logging in can safely retry here.
          if (error instanceof Error && error.message === 'chatgpt_login_required') this.voiceReusable = true;
          throw error;
        }
      },
      stop: () => transport.stop(),
      speak: text => transport.speak(text),
      appendText: text => transport.appendText(text),
      dispose: () => {
        transport.dispose();
        if (this.voice === transport) {
          this.voice = undefined;
          if (!this.voiceStarted) this.voiceReusable = true;
        }
      },
    };
  }
  private turn: { id: string; resolve(r: TurnResult): void; awaitingId?: boolean; notifications?: Array<{ method: string; params: any; turnId: string }>; error?: string; compact?: boolean; goal?: boolean; goalTurns?: number; cancelRequested?: boolean } | null = null;
  private readonly finishedTurns = new Set<string>();
  private goal: any = null;
  /** Where the hub's goal card hears about changes; set right after construction. */
  onGoal?: (goal: GoalSnapshot | null) => void;
  private pushGoal(): void {
    this.onGoal?.(goalSnapshot(this.goal));
  }
  async goalState(): Promise<GoalSnapshot | null> {
    try {
      this.goal = (await this.peer.request('thread/goal/get', { threadId: this.agentSessionId })).goal ?? null;
    } catch {
      // The server is gone or has no goals; whatever notifications left stands.
    }
    return goalSnapshot(this.goal);
  }
  private goalSubmission: Promise<unknown> | null = null;
  /** Command output streamed so far, so each update carries the whole text like other agents do. */
  private readonly outputSoFar = new Map<string, string>();
  private readonly messageSoFar = new Map<string, string>();
  /** File changes by item id, for approval cards that need to show the patch. */
  private readonly fileChanges = new Map<string, any[]>();
  private readonly computerCalls = new Set<string>();
  /** Settings sent with turn/start, or persisted before runtime-owned goal turns. */
  private workMode: WorkMode = 'execute';
  private collaboration: any = undefined;
  private defaultModel = '';
  private defaultEffort: string | null = null;
  private overrides: { serviceTier?: string | null; model?: string; effort?: EffortLevel; approvalPolicy?: string; approvalsReviewer?: ApprovalsReviewer; sandboxPolicy?: { type: string } } = {};

  constructor(
    readonly agentSessionId: string,
    private readonly peer: JsonRpcPeer,
    private readonly cwd: string,
    initialModel?: string,
    initialEffort?: string | null,
    /** Whose app-server this is; TRAE CLI runs the same protocol under its own name. */
    private readonly name = 'codex',
    private readonly computer?: BrowserComputer,
    /** Send image bytes when this server's CDN upload references are unusable. */
    private readonly inlineImages = false,
  ) { this.defaultModel = initialModel ?? ''; this.defaultEffort = initialEffort ?? null; }

  /** Called by the peer for every notification. Ignores anything about another thread. */
  onNotification(method: string, params: any): void {
    if (params?.threadId !== undefined && params.threadId !== this.agentSessionId) return;
    const nativeId = params?.turnId ?? (method === 'turn/started' || method === 'turn/completed' ? params?.turn?.id : undefined);
    if (nativeId && this.finishedTurns.has(String(nativeId))) return;
    if (method.startsWith('thread/realtime/')) {
      // Only a positively observed native close permits reusing this notification source.
      if (method === 'thread/realtime/closed' && this.voice) this.voiceReusable = true;
      this.voice?.handleNotification(method, params);
      return;
    }
    // Only started can provisionally claim an id. Hold content until the RPC confirms it,
    // so a mismatched claim cannot leak items or settle the wrong turn.
    const pending = this.turn;
    if (pending?.awaitingId && (nativeId || /^(item|turn)\//.test(method) || method === 'thread/tokenUsage/updated' || method === 'error')) {
      if (method === 'turn/started' && params?.turn?.id && !pending.id) pending.id = String(params.turn.id);
      const notifications = pending.notifications ??= [];
      if (notifications.length === MAX_PENDING_NOTIFICATIONS) notifications.shift();
      notifications.push({ method, params, turnId: nativeId ? String(nativeId) : pending.id });
      return;
    }
    if (this.turn?.id && nativeId && String(nativeId) !== this.turn.id) return;
    switch (method) {
      case 'serverRequest/resolved':
        this.hooks?.cancelInput?.(String(params.requestId));
        break;
      case 'item/plan/delta':
        this.emit({ type: 'plan.delta', data: { id: params.itemId, text: params.delta } });
        break;
      case 'queue/status':
        this.emit({
          type: 'turn.phase',
          data: {
            phase: 'model_queue',
            state: typeof params?.state === 'string' ? params.state : 'queued',
            operation: typeof params?.operation === 'string' ? params.operation : null,
            position: typeof params?.position === 'number' && Number.isFinite(params.position) ? params.position : null,
            message: typeof params?.message === 'string' ? params.message : null,
          },
        });
        break;
      case 'turn/started':
        if (!this.turn && !this.hooks && this.sessionHooks && params?.turn?.id) {
          const id = String(params.turn.id);
          this.hooks = this.sessionHooks.turnStarted(id);
          this.turn = { id, resolve: result => this.sessionHooks?.turnFinished(id, result) };
        }
        if ((this.turn?.compact || this.turn?.goal) && !this.turn.id && params?.turn?.id) {
          if (this.turn.goal && (this.turn.goalTurns ?? 0) > 0) this.emit({ type: 'message.delta', data: { text: '\n\n' } });
          this.turn.goalTurns = (this.turn.goalTurns ?? 0) + 1;
          this.turn.id = String(params.turn.id);
          if (this.turn.cancelRequested) void this.cancel();
        }
        break;
      case 'thread/goal/updated':
        this.goal = params.goal;
        this.pushGoal();
        if (this.turn && !this.turn.compact && this.goal?.status === 'active') this.turn.goal = true;
        this.finishGoalIfIdle();
        break;
      case 'thread/goal/cleared':
        this.goal = null;
        this.pushGoal();
        this.finishGoalIfIdle();
        break;
      case 'item/started': {
        if (params?.item?.type === 'agentMessage' && typeof params.item.id === 'string') {
          this.messageSoFar.set(params.item.id, '');
        }
        if (this.turn?.compact && params?.item?.type === 'contextCompaction') break;
        if (params?.item?.type === 'fileChange') this.fileChanges.set(params.item.id, params.item.changes ?? []);
        if (params?.item?.type === 'plan') break; // Proposed plan text arrives separately from progress steps.
        const ev = mapCodexItemStart(params?.item);
        if (ev) this.emit(ev);
        break;
      }
      case 'item/completed': {
        if (params?.item?.type === 'agentMessage' && typeof params.item.text === 'string'
          && typeof params.item.id === 'string') {
          const id = params.item.id;
          const previous = this.messageSoFar.get(id) ?? '';
          const text = params.item.text;
          if (text !== previous) {
            this.emit(previous
              ? { type: 'message.replace', data: { id, previous, text } }
              : { type: 'message.delta', data: { id, text } });
          }
          this.messageSoFar.set(id, text);
          break;
        }
        if (params?.item?.type === 'plan') {
          this.emit({ type: 'plan.completed', data: { id: params.item.id, text: params.item.text ?? '' } });
          break;
        }
        if (this.turn?.compact && params?.item?.type === 'contextCompaction') break;
        if (params?.item?.type === 'exitedReviewMode' && params.item.review) {
          this.emit({ type: 'message.delta', data: { text: String(params.item.review) } });
        }
        if (params?.item?.type === 'fileChange') this.fileChanges.set(params.item.id, params.item.changes ?? []);
        const ev = mapCodexItemEnd(params?.item);
        if (ev) this.emit(ev);
        break;
      }
      case 'item/agentMessage/delta':
        if (params?.delta) {
          const text = String(params.delta);
          if (typeof params.itemId === 'string') {
            this.messageSoFar.set(params.itemId, (this.messageSoFar.get(params.itemId) ?? '') + text);
          }
          this.emit({ type: 'message.delta', data: { text } });
        }
        break;
      case 'item/reasoning/summaryTextDelta':
      case 'item/reasoning/textDelta':
        if (params?.delta) this.emit({ type: 'reasoning.delta', data: { text: String(params.delta) } });
        break;
      case 'item/commandExecution/outputDelta': {
        const soFar = this.outputSoFar.get(params.itemId) ?? '';
        const text = soFar + String(params.delta ?? '');
        this.outputSoFar.set(params.itemId, text);
        this.emit({ type: 'tool.update', data: { id: params.itemId, status: 'in_progress', output: text } });
        break;
      }
      case 'item/fileChange/patchUpdated': {
        if (Array.isArray(params?.changes)) this.fileChanges.set(params.itemId, params.changes);
        const diffs = params.changes.map(codexDiff).filter(Boolean);
        if (diffs.length) this.emit({ type: 'tool.update', data: { id: params.itemId, status: 'in_progress', content: diffs } });
        break;
      }
      case 'turn/plan/updated': {
        const entries = planEntries(params?.plan);
        if (entries.length) this.emit({ type: 'plan.updated', data: { entries } });
        break;
      }
      case 'thread/tokenUsage/updated': {
        const usage = params?.tokenUsage;
        const sample = this.accounting.sample(usage);
        if (sample) this.emit({ type: 'usage.sample', data: { ...sample } });
        // `total` accumulates billed tokens across requests; `last` measures the active context.
        const used = usage?.last?.totalTokens;
        const size = usage?.modelContextWindow;
        if (typeof used === 'number' && Number.isFinite(used) && used >= 0 && typeof size === 'number' && Number.isFinite(size) && size > 0) {
          this.emit({ type: 'usage.updated', data: { used, size } });
        }
        break;
      }
      case 'error':
        // A failed turn also gets turn/completed; a retry does not end it, so only the last counts.
        if (this.turn && params?.turnId === this.turn.id && !params?.willRetry && params?.error?.message) {
          this.turn.error = String(params.error.message);
        }
        break;
      case 'turn/completed': {
        const turn = this.turn;
        if (!turn || params?.turn?.id !== turn.id) break;
        this.rememberFinishedTurn(turn.id);
        if (params.turn.status === 'failed') turn.error ??= params.turn.error?.message ? String(params.turn.error.message) : 'Codex turn failed';
        if (turn.goal && params.turn.status === 'completed') {
          turn.id = '';
          this.outputSoFar.clear();
          this.messageSoFar.clear();
          this.finishGoalIfIdle();
          break;
        }
        this.finishTurn(stopReasonOf(String(params.turn.status ?? 'completed')));
        break;
      }
      default:
        break; // thread bookkeeping, MCP startup, rate limits: nothing the thread view needs
    }
  }

  /** Called by the peer when Codex asks something. Approvals go to the user; the rest is refused. */
  async onRequest(method: string, params: any, requestId?: string | number): Promise<unknown> {
    if (params?.threadId && params.threadId !== this.agentSessionId) return undefined;
    if (params?.turnId && this.turn?.id && params.turnId !== this.turn.id) return undefined;
    if (method === 'item/tool/call' && this.computer) {
      const turn = this.turn;
      const fail = (text: string) => ({ success: false, contentItems: [{ type: 'inputText', text }] });
      if (!turn || turn.cancelRequested || params?.threadId !== this.agentSessionId || !params?.turnId
        || params.tool !== 'calli_computer' || params.namespace || typeof params.callId !== 'string') return fail('Unknown or inactive browser tool call.');
      if (this.computerCalls.has(params.callId)) return fail('Duplicate browser call ID; the action was not repeated.');
      this.computerCalls.add(params.callId);
      try {
        const result = await this.computer.execute(params.arguments);
        if (this.turn !== turn || turn.cancelRequested) return fail('Browser task was cancelled.');
        this.emit({ type: 'tool.end', data: { id: params.callId, status: 'completed', computer: result.evidence,
          output: JSON.stringify(result.evidence) } });
        return { success: true, contentItems: [
          { type: 'inputText', text: JSON.stringify(result.evidence) },
          { type: 'inputImage', imageUrl: `data:image/png;base64,${result.image.toString('base64')}` },
        ] };
      } catch (error) {
        const text = error instanceof Error ? error.message : String(error);
        if (this.turn === turn && !turn.cancelRequested) this.emit({ type: 'tool.end', data: { id: params.callId, status: 'failed', output: text } });
        return fail(text);
      }
    }
    if (method === 'item/tool/requestUserInput') {
      const questions = nativeQuestions(params?.questions);
      const answer = questions.length && this.hooks?.requestInput
        ? await this.hooks.requestInput({ id: String(requestId), title: '需要你的意见', questions }) : { cancelled: true } as const;
      return { answers: 'answers' in answer ? Object.fromEntries(Object.entries(answer.answers).map(([id, answers]) => [id, { answers }])) : {} };
    }
    if (method !== 'item/commandExecution/requestApproval' && method !== 'item/fileChange/requestApproval') {
      return undefined; // answered with "method not supported", which Codex treats as declined
    }
    const isCommand = method === 'item/commandExecution/requestApproval';
    const options = codexApprovalOptions(params?.availableDecisions);
    const req: ApprovalRequest = {
      title: isCommand ? '运行命令' : '修改文件',
      options,
      toolCall: isCommand
        ? { kind: 'command', command: params?.command, cwd: params?.cwd, rawInput: { command: params?.command } }
        : { kind: 'fileChange', changes: this.fileChanges.get(params?.itemId) ?? [], content: (this.fileChanges.get(params?.itemId) ?? []).map(codexDiff).filter(Boolean) },
    };
    const decision = this.hooks ? await this.hooks.requestApproval(req) : ({ cancelled: true } as ApprovalDecision);
    return codexApprovalDecision(decision, params);
  }

  private emit(ev: EmittedEvent): void {
    // Manual /compact has its own tracked operation. Items during ordinary turns are automatic.
    if (ev.type === 'context.compaction') ev = { ...ev, data: { ...ev.data, automatic: true } };
    if (this.hooks) this.hooks.emit(ev);
  }

  private finishTurn(stopReason: string): void {
    const turn = this.turn;
    if (!turn) return;
    if (turn.compact) {
      if (stopReason === 'end_turn') this.emit({ type: 'message.delta', data: { text: '上下文压缩完成。' } });
    }
    if (!turn.awaitingId) this.rememberFinishedTurn(turn.id);
    this.turn = null;
    this.hooks = null;
    this.outputSoFar.clear();
    this.messageSoFar.clear();
    const result = { stopReason, ...(stopReason === 'error' && turn.error ? { error: turn.error } : {}) };
    if (this.computer) void this.computer.close().finally(() => turn.resolve(result));
    else turn.resolve(result);
  }

  private rememberFinishedTurn(id: string): void {
    if (!id) return;
    this.finishedTurns.add(id);
    if (this.finishedTurns.size > MAX_FINISHED_TURNS) this.finishedTurns.delete(this.finishedTurns.values().next().value!);
  }

  /** The settings the thread carries, sent with the next turn so the live session picks them up. */
  private turnOverrides(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    if (this.overrides.model) out.model = this.overrides.model;
    if (this.overrides.serviceTier !== undefined) out.serviceTier = this.overrides.serviceTier;
    if (this.overrides.effort) out.effort = this.overrides.effort;
    if (this.overrides.approvalPolicy) out.approvalPolicy = this.overrides.approvalPolicy;
    if (this.overrides.approvalsReviewer) out.approvalsReviewer = this.overrides.approvalsReviewer;
    if (this.overrides.sandboxPolicy) out.sandboxPolicy = this.overrides.sandboxPolicy;
    if (this.collaboration) out.collaborationMode = { mode: this.workMode === 'plan' ? 'plan' : 'default', settings: {
      model: this.overrides.model ?? this.defaultModel, reasoning_effort: this.overrides.effort ?? this.defaultEffort, developer_instructions: null,
    } };
    return out;
  }

  async setWorkMode(mode: WorkMode): Promise<void> {
    if (this.collaboration !== undefined && this.workMode === mode) return;
    const modes = await this.peer.request('collaborationMode/list', {}).catch(() => ({ data: [] }));
    const preset = (modes.data ?? []).find((m: any) => m.mode === (mode === 'plan' ? 'plan' : 'default'));
    if (!preset && mode === 'plan') throw new Error('此版本不支持原生计划模式，请升级智能体');
    if (preset && !this.defaultModel) {
      const result = await this.peer.request('model/list', {});
      const model = result.data?.find((m: any) => m.isDefault) ?? result.data?.find((m: any) => !m.hidden);
      this.defaultModel = preset.model ?? model?.model ?? model?.id;
      if (!this.defaultModel) throw new Error('无法读取计划模式使用的模型');
    }
    if (mode === 'plan' && this.goal?.status === 'active') throw new Error('请先暂停当前目标，再进入计划模式');
    this.workMode = mode;
    this.collaboration = preset ?? null;
  }

  async prompt(input: TurnInput, hooks: TurnHooks): Promise<TurnResult> {
    if (this.turn) throw new Error('a turn is already running');
    this.hooks = hooks;

    const goalCommand = parseGoalCommand(input.text);
    if (goalCommand) {
      if (input.images.length) { this.hooks = null; return { stopReason: 'error', error: '/goal 不接受图片。' }; }
      return this.promptGoal(goalCommand, hooks);
    }

    const command = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(input.text.trim());
    const name = command?.[1];
    const args = command?.[2]?.trim() ?? '';
    if (name === 'compact' && !input.images.length && !args) {
      return trackCompaction(hooks, async () => {
        let resolveCompact!: (result: TurnResult) => void;
        const result = new Promise<TurnResult>((resolve) => { resolveCompact = resolve; });
        // Register before the RPC: notifications may arrive before its acknowledgement.
        this.turn = { id: '', resolve: resolveCompact, compact: true };
        try {
          await this.peer.request('thread/compact/start', { threadId: this.agentSessionId });
        } catch (error) {
          if (this.turn?.compact) {
            this.turn.error = error instanceof Error ? error.message : String(error);
            this.finishTurn('error');
          }
        }
        return result;
      });
    }
    if (name && CODEX_LOCAL_COMMANDS.some((c) => c.name === name) && name !== 'review') {
      try {
        if (input.images.length || args) throw new Error(`/${name} 不接受参数或图片。`);
        let text: string;
        if (name === 'skills') {
          const skills = await readSkills(this.peer, this.cwd);
          text = skills.length ? skills.map((s) => `- /${s.name}：${s.description}`).join('\n') : '当前目录没有可用技能。';
        } else if (name === 'mcp') {
          const lines: string[] = [];
          let cursor: string | undefined;
          do {
            const res = await this.peer.request('mcpServerStatus/list', { ...(cursor ? { cursor } : {}) });
            for (const server of res?.data ?? []) {
              const auth: Record<string, string> = { unsupported: '不支持认证', notLoggedIn: '未登录', bearerToken: '令牌认证', oAuth: '已登录' };
              lines.push(`- ${server.name}：${Object.keys(server.tools ?? {}).length} 个工具 · ${auth[server.authStatus] ?? '认证状态未知'}`);
            }
            cursor = res?.nextCursor ?? undefined;
          } while (cursor);
          text = lines.length ? lines.join('\n') : '没有配置 MCP 服务。';
        } else {
          const quota = codexQuota(await this.peer.request('account/rateLimits/read', {}));
          text = quota.windows.length ? quota.windows.map((w) => `- ${w.scope ? `${w.scope} · ` : ''}${w.label}：已用 ${w.usedPercent}%${w.resetsAt ? `，重置于 ${w.resetsAt}` : ''}`).join('\n') : '当前账号没有可用的套餐额度信息。';
        }
        hooks.emit({ type: 'message.delta', data: { text } });
        return { stopReason: 'end_turn' };
      } catch (err) {
        return { stopReason: 'error', error: err instanceof Error ? err.message : String(err) };
      } finally {
        this.hooks = null;
      }
    }

    let resolveTurn!: (r: TurnResult) => void;
    const result = new Promise<TurnResult>((resolve) => {
      resolveTurn = resolve;
    });
    const pending: NonNullable<CodexSession['turn']> = { id: '', resolve: resolveTurn, awaitingId: true, goal: this.goal?.status === 'active', goalTurns: 1 };
    this.turn = pending;
    try {
      let skill: CodexSkill | undefined;
      if (name && name !== 'review') {
        skill = (await readSkills(this.peer, this.cwd)).find((s) => s.name === name);
        if (!skill) throw new Error(`未知命令 /${name}，请在输入框输入 / 查看可用操作和技能。`);
      }
      if (name === 'review' && input.images.length) throw new Error('/review 不接受图片。');
      const res = name === 'review' ? await this.peer.request('review/start', {
        threadId: this.agentSessionId,
        delivery: 'inline',
        target: args ? { type: 'custom', instructions: args } : { type: 'uncommittedChanges' },
      }) : await this.peer.request('turn/start', {
        threadId: this.agentSessionId,
        input: [
          ...(input.text ? [{ type: 'text', text: skill ? `$${skill.name}${args ? ` ${args}` : ''}` : input.text }] : []),
          ...(skill ? [{ type: 'skill', name: skill.name, path: skill.path }] : []),
          ...(this.inlineImages
            ? input.images.map((img) => ({ type: 'image', url: `data:${img.mimeType};base64,${img.data}` }))
            : input.images.filter((img) => typeof img.path === 'string').map((img) => ({ type: 'localImage', path: img.path }))),
        ],
        ...this.turnOverrides(),
      });
      const id = String(res?.turn?.id ?? '');
      if (!id) throw new Error('Codex did not return a turn id');
      // The RPC id is authoritative; replay only its buffered notifications.
      if (this.turn === pending && pending.awaitingId) {
        if (pending.id && pending.id !== id) console.warn(`[${this.name}] turn/start id mismatch; discarded unmatched notifications`);
        pending.id = id;
        pending.awaitingId = false;
        const notifications = pending.notifications ?? [];
        pending.notifications = undefined;
        if (pending.cancelRequested) void this.cancel();
        for (const notification of notifications) {
          if (notification.turnId === id) this.onNotification(notification.method, notification.params);
        }
      }
    } catch (err) {
      if (this.turn === pending) {
        pending.error = err instanceof Error ? err.message : String(err);
        this.finishTurn('error');
      }
    }
    return result;
  }

  /** Model, effort and permission level all ride along with the next turn, so they apply in place. */
  async setModel(model: string): Promise<boolean> {
    this.overrides.model = model;
    if (this.turn?.goal) await this.peer.request('thread/settings/update', { threadId: this.agentSessionId, model });
    return true;
  }

  async setSpeed(speed: string | null): Promise<boolean> {
    await this.peer.request('thread/settings/update', { threadId: this.agentSessionId,
      ...(this.overrides.model ? { model: this.overrides.model } : {}), serviceTier: speed });
    this.overrides.serviceTier = speed;
    return true;
  }

  async setEffort(effort: EffortLevel): Promise<boolean> {
    this.overrides.effort = effort;
    if (this.turn?.goal) await this.peer.request('thread/settings/update', { threadId: this.agentSessionId, effort });
    return true;
  }

  async setPermissionMode(mode: PermissionMode): Promise<void> {
    const codex = CODEX_MODES[mode];
    this.overrides.approvalPolicy = codex.approvalPolicy;
    this.overrides.approvalsReviewer = codex.approvalsReviewer;
    this.overrides.sandboxPolicy = SANDBOX_POLICY[codex.sandbox];
    // Native app-server only permits changing the reviewer on an active turn. The sandbox and
    // approval policy intentionally remain unchanged until the next turn starts.
    if (this.turn?.id) await this.peer.request('turn/settings/update', { threadId: this.agentSessionId,
      turnId: this.turn.id, approvalsReviewer: codex.approvalsReviewer });
    if (this.turn?.goal) await this.peer.request('thread/settings/update', { threadId: this.agentSessionId, approvalPolicy: codex.approvalPolicy,
      approvalsReviewer: codex.approvalsReviewer, sandboxPolicy: this.overrides.sandboxPolicy });
  }

  async cancel(): Promise<void> {
    const turn = this.turn;
    if (!turn) return;
    if (this.computer) { turn.cancelRequested = true; await this.computer.close(); }
    if (turn.goal) {
      turn.cancelRequested = true;
      await this.goalSubmission;
      if (this.turn !== turn) return;
      if (this.goal) this.goal = (await this.peer.request('thread/goal/set', { threadId: this.agentSessionId, status: 'paused' })).goal ?? this.goal;
      if (this.turn !== turn) return;
      this.pushGoal();
      if (!turn.id) { this.finishTurn('cancelled'); return; }
    }
    if (!turn.id) { turn.cancelRequested = true; return; }
    await this.peer.request('turn/interrupt', { threadId: this.agentSessionId, turnId: turn.id }).catch(() => undefined);
  }

  private finishGoalIfIdle(): void {
    if (!this.turn?.goal || this.turn.id || this.goal?.status === 'active') return;
    this.emit({ type: 'message.delta', data: { text: `\n\n${goalSummary(this.goal)}` } });
    this.finishTurn(this.turn.cancelRequested ? 'cancelled' : 'end_turn');
  }

  async controlGoal(action: GoalControl): Promise<string> {
    await this.goalSubmission;
    if (action === 'show') {
      this.goal = (await this.peer.request('thread/goal/get', { threadId: this.agentSessionId })).goal;
      this.pushGoal();
      return goalSummary(this.goal);
    }
    if (action === 'pause') {
      if (this.turn?.goal) await this.cancel();
      else this.goal = (await this.peer.request('thread/goal/set', { threadId: this.agentSessionId, status: 'paused' })).goal ?? this.goal;
      this.pushGoal();
      return '目标已暂停。';
    }
    await this.peer.request('thread/goal/clear', { threadId: this.agentSessionId });
    this.goal = null;
    this.pushGoal();
    this.finishGoalIfIdle();
    return '目标已清除；当前轮仍可用停止按钮结束。';
  }

  private async promptGoal(command: GoalCommand, hooks: TurnHooks): Promise<TurnResult> {
    if (command.action !== 'set' && command.action !== 'resume') {
      try {
        let text: string;
        if (command.action === 'pause') {
          this.goal = (await this.peer.request('thread/goal/set', { threadId: this.agentSessionId, status: 'paused' })).goal;
          this.pushGoal();
          text = goalSummary(this.goal);
        } else {
          text = await this.controlGoal(command.action);
        }
        hooks.emit({ type: 'message.delta', data: { text } });
        return { stopReason: 'end_turn' };
      } catch (error) { return { stopReason: 'error', error: String(error) }; }
      finally { this.hooks = null; }
    }
    if (command.objective && command.objective.length > 4000) {
      this.hooks = null;
      return { stopReason: 'error', error: '目标最多 4000 个字符。' };
    }
    let resolve!: (result: TurnResult) => void;
    const result = new Promise<TurnResult>((r) => { resolve = r; });
    const previousGoal = this.goal;
    // Optimistic for a new objective; a resume keeps the old one until the server answers.
    if (command.objective) {
      this.goal = { objective: command.objective, status: 'active' };
      this.pushGoal();
    }
    this.turn = { id: '', resolve, goal: true };
    this.goalSubmission = (async () => {
      // Goal turns are started by the runtime, so turn/start overrides never reach them.
      await this.peer.request('thread/settings/update', { threadId: this.agentSessionId, ...this.turnOverrides() });
      const res = await this.peer.request('thread/goal/set', {
        threadId: this.agentSessionId, status: 'active',
        ...(command.objective ? { objective: command.objective } : {}),
      });
      this.goal = res?.goal ?? this.goal;
      this.pushGoal();
      return res;
    })();
    try { await this.goalSubmission; }
    catch (error) {
      this.goal = previousGoal;
      this.pushGoal();
      if (this.turn) { this.turn.error = String(error); this.finishTurn('error'); }
    }
    finally { this.goalSubmission = null; }
    return result;
  }

  async close(): Promise<void> {
    this.voice?.handleClosed();
    this.onClosed();
    this.peer.close();
    await this.computer?.close();
  }

  /** The peer is gone: a running turn can only end as an error now. */
  onClosed(): void {
    this.voice?.handleClosed();
    void this.computer?.close();
    if (this.turn) {
      this.turn.error ??= `${this.name} app-server exited`;
      this.finishTurn('error');
    }
  }
}

export const CODEX_METADATA = {
  adapter: 'codex' as const,
  label: 'Codex',
  icon: CODEX_ICON,
  installGuide: 'npm install -g @openai/codex',
};

/** How long a model probe is reused, so several threads opening at once start one process. */
export class CodexAppServerAdapter implements AgentAdapter {
  readonly borrowedTools = [codexSearchTool, codexReadTool, codexComputerTool];
  readonly id = 'codex';
  readonly label = CODEX_METADATA.label;
  readonly icon = CODEX_METADATA.icon;
  readonly installGuide = CODEX_METADATA.installGuide;
  readonly capabilities = {
    memory: true,
    realtimeVoice: true,
    workModes: ['execute', 'plan'] as const,
    speed: true,
    goal: { controls: ['show', 'pause', 'clear'] as const, actions: ['set', 'show', 'pause', 'resume', 'clear'] as const, live: true },
    coordination: true,
    resume: true,
    modelSwitch: true,
    approvals: true,
    permissionModes: ['ask', 'auto_edit', 'full_access'] as const,
    // The widest set; each model says which of these it really takes.
    effortLevels: ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'] as const,
    images: true,
    deleteSession: true,
    history: true,
    fork: true,
    forkAt: true,
    editMessage: true,
    multiAccount: false,
    // A thread is resumed with the directory to work in, whatever directory it was made in.
    forkInto: true,
    quota: true,
    compact: true,
    systemPrompt: true,
  };

  private modelProbe: Promise<ModelOption[]> | null = null;

  constructor(
    private readonly command: string[],
    /** Extra environment for this agent instance. */
    private readonly env: Record<string, string> = {},
    /** Name in errors; TRAE CLI reuses this adapter with its own binary. */
    private readonly name = 'codex',
    private readonly inlineImages = false,
  ) {}

  /** Device auth prints a code, so it also works when the terminal is not on the browser's machine. */
  loginCommand(): string {
    const home = this.env.CODEX_HOME;
    const prefix = home ? `env CODEX_HOME=${quoteArg(home)} ` : '';
    return `${prefix}${quoteArg(this.command[0])} login --device-auth`;
  }

  async realtimeSupport(): Promise<{ supported: boolean; login?: 'ready' | 'required' }> {
    return this.withServer(async peer => {
      try {
        const result = await peer.request('thread/realtime/listVoices', {});
        if (!result || typeof result.voices !== 'object' || result.voices === null) return { supported: false };
        const account = await peer.request('account/read', { refreshToken: false });
        return { supported: true, login: account?.account?.type === 'chatgpt' ? 'ready' : 'required' };
      } catch (error) {
        if (error instanceof RpcError && error.code === -32601) return { supported: false };
        throw new Error('voice_probe_failed');
      }
    }, tmpdir(), 8000);
  }

  diagnose() { return cliDiagnostic(this.command, this.env, 'app-server'); }

  async available(): Promise<boolean> {
    return commandExists(this.command[0]);
  }

  /** The models this account offers, from one short-lived server that opens no thread. */
  models(refresh = false): Promise<ModelOption[]> {
    if (refresh) this.modelProbe = null;
    this.modelProbe ??= this.readModels().catch((err) => {
      this.modelProbe = null;
      throw err;
    });
    return this.modelProbe;
  }

  private async readModels(): Promise<ModelOption[]> {
    return this.withServer(async (peer) => {
      const models: any[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < 20; page++) {
        const res = await peer.request('model/list', cursor ? { cursor } : {});
        models.push(...(res?.data ?? []));
        cursor = res?.nextCursor ?? undefined;
        if (!cursor) break;
      }
      return codexModelOptions(models);
    }, tmpdir(), 30000);
  }

  async commands(cwd?: string): Promise<SlashCommand[]> {
    if (!cwd) return CODEX_LOCAL_COMMANDS;
    try {
      const skills = await this.withServer((peer) => readSkills(peer, cwd), cwd);
      return [...CODEX_LOCAL_COMMANDS, ...skills.map((s): SlashCommand => ({ name: s.name, description: s.description, kind: 'skill', hint: '[任务说明]' }))];
    } catch (err) {
      // Older app-servers may not expose skills; their built-in commands still work.
      if (err instanceof RpcError && err.code === -32601) return CODEX_LOCAL_COMMANDS;
      throw err;
    }
  }

  async fetchQuota(): Promise<PlanQuota> {
    return this.withServer(async (peer) => codexQuota(await peer.request('account/rateLimits/read', {})));
  }

  resumeCommand(agentSessionId: string): string {
    return `codex resume ${agentSessionId}`;
  }

  /** "You've hit your usage limit. … try again at …" */
  planExhausted(message: string): boolean {
    return /hit your (?:\w+ )?limit|usage limit/i.test(message);
  }

  async sessionLocation(agentSessionId: string | null, cwd: string) {
    const configDir = configDirectory(this.env, 'CODEX_HOME', ['.codex'], cwd);
    const matches = (name: string, id: string) => name.startsWith('rollout-') && name.endsWith(`-${id}.jsonl`);
    const sessionPath = await findSessionFile(join(configDir, 'sessions'), agentSessionId, 3, matches)
      ?? await findSessionFile(join(configDir, 'archived_sessions'), agentSessionId, 3, matches);
    return { configDir, sessionPath, ...(agentSessionId ? { resumeCommand: `env CODEX_HOME=${quoteArg(configDir)} codex resume ${quoteArg(agentSessionId)}` } : {}) };
  }

  /** The base instructions the conversation was started with, from the rollout's first record. */
  async readSystemPrompt(agentSessionId: string, cwd: string): Promise<SystemPrompt | null> {
    const { sessionPath } = await this.sessionLocation(agentSessionId, cwd);
    if (!sessionPath) return null;
    let text: string | null = null;
    await scanJsonl(sessionPath, (line) => {
      const found = codexSystemPromptMeta(line);
      if (found !== null) text = found;
    });
    return text ? { text, source: 'Codex 会话文件' } : null;
  }

  memoryReader() {
    return new CodexMemoryReader(join(configDirectory(this.env, 'CODEX_HOME', ['.codex'], process.cwd()), 'memories'));
  }

  skillDirs(): SkillDir[] {
    // Include shared user skills while installing into the selected instance's private folder.
    return [
      { path: join(configDirectory(this.env, 'CODEX_HOME', ['.codex'], process.cwd()), 'skills'), scope: 'personal' },
      { path: join(this.env.HOME ?? process.env.HOME ?? homedir(), '.agents', 'skills'), scope: 'shared' },
    ];
  }

  /**
   * MCP servers live as `[mcp_servers.<name>]` tables in config.toml. Writes replace exactly one
   * server's tables and leave every other line of the file — profiles, providers, comments — as
   * it stood.
   */
  private configTomlPath(): string {
    return join(configDirectory(this.env, 'CODEX_HOME', ['.codex'], process.cwd()), 'config.toml');
  }

  mcpServers(): McpServerInfo[] {
    return tomlMcpServers(this.configTomlPath());
  }

  mcpInstall(name: string, config: Record<string, unknown>): void {
    installTomlMcpServer(this.configTomlPath(), name, config);
  }

  mcpRemove(name: string): void {
    if (!removeTomlMcpServer(this.configTomlPath(), name)) throw new Error(`${this.name} 没有名为 ${name} 的 MCP 服务`);
  }

  async deleteSession(agentSessionId: string): Promise<void> {
    await this.withServer((peer) => peer.request('thread/delete', { threadId: agentSessionId }));
  }

  /** Share concurrent list requests without retaining a stale snapshot of native sessions. */
  private sessionListRead: Promise<AgentSessionSummary[]> | null = null;

  /** Read the index only: the default list call scans rollouts to repair metadata on every page. */
  listSessions(): Promise<AgentSessionSummary[]> {
    this.sessionListRead ??= this.readSessionIndex().finally(() => { this.sessionListRead = null; });
    return this.sessionListRead;
  }

  private async readSessionIndex(): Promise<AgentSessionSummary[]> {
    return this.withServer(async (peer) => {
      const out: AgentSessionSummary[] = [];
      const cursors = new Set<string>();
      let cursor: string | undefined;
      // Include terminal and exec sessions, but keep sub-agent scratch threads out.
      for (;;) {
        const res = await peer.request('thread/list', {
          limit: 500, sourceKinds: SESSION_SOURCES, useStateDbOnly: true, sortKey: 'updated_at',
          ...(cursor ? { cursor } : {}),
        });
        for (const t of res?.data ?? []) out.push(codexSessionSummary(t));
        cursor = res?.nextCursor ?? undefined;
        if (!cursor) break;
        // Fail visibly instead of returning a silently truncated list (and incomplete search).
        if (cursors.has(cursor)) throw new Error('Codex returned a repeated session cursor');
        cursors.add(cursor);
      }
      return out;
    }, tmpdir(), 30_000);
  }

  async getSession(agentSessionId: string): Promise<AgentSessionSummary | null> {
    try {
      return await this.withServer(async (peer) => {
        const res = await peer.request('thread/read', { threadId: agentSessionId, includeTurns: false });
        const thread = res?.thread;
        return thread && SESSION_SOURCES.includes(thread.source) ? codexSessionSummary(thread) : null;
      }, tmpdir(), 30_000);
    } catch (err) {
      if (err instanceof RpcError && err.code === -32602) return null;
      throw err;
    }
  }

  /** Reads a conversation's turns. Reading does not open the thread, so it cannot change it. */
  async loadHistory(agentSessionId: string): Promise<HistoryTurn[]> {
    return this.withServer(async (peer) => {
      const turns: any[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < 200; page++) {
        const res = await peer.request('thread/turns/list', {
          threadId: agentSessionId,
          itemsView: 'full',
          sortDirection: 'asc',
          ...(cursor ? { cursor } : {}),
        });
        turns.push(...(res?.data ?? []));
        cursor = res?.nextCursor ?? undefined;
        if (!cursor) break;
      }
      return turnsFromCodexHistory(turns);
    });
  }

  async forkSession(agentSessionId: string, _cwd?: string, forkRef?: string, intoCwd?: string): Promise<string> {
    return this.withServer(async (peer) => {
      const res = await peer.request('thread/fork', {
        threadId: agentSessionId,
        // Turns after this one are left out of the copy.
        ...(forkRef ? { lastTurnId: forkRef } : {}),
        // Where the copy will work, so its own record says so too. Resuming passes it again.
        ...(intoCwd ? { cwd: intoCwd } : {}),
        ephemeral: false,
        excludeTurns: true,
      });
      const id = res?.thread?.id;
      if (!id) throw new Error('Codex did not return a thread id for the fork');
      return String(id);
    });
  }

  /**
   * Another account's rollout, forked into this account through the app-server's fork-by-path. The
   * cleaned rollout is staged under this account's own `sessions/` first — the fork's lineage names
   * it as its source, so it stays; without it the fork cannot be resumed. Encrypted reasoning only
   * the account that wrote it can read is left out of the copy; a conversation compacted natively
   * cannot go over at all, its summary being sealed the same way.
   */
  async importSession(sourcePath: string): Promise<string> {
    const dir = join(configDirectory(this.env, 'CODEX_HOME', ['.codex'], process.cwd()), 'sessions', 'imported');
    const path = join(dir, basename(sourcePath));
    await mkdir(dir, { recursive: true });
    await writeFile(path, portableRollout(await readFile(sourcePath, 'utf8')), { mode: 0o600 });
    return await this.withServer(async (peer) => {
      const res = await peer.request('thread/fork', { threadId: '', path, ephemeral: false, excludeTurns: true });
      const id = res?.thread?.id;
      if (!id) throw new Error(`${this.name} did not return a thread id for the copy`);
      return String(id);
    });
  }

  /** A copy that stops before one turn, excluding it and everything after it. */
  async forkBeforeSession(agentSessionId: string, _cwd: string, forkBeforeRef: string): Promise<string> {
    return this.withServer(async (peer) => {
      const res = await peer.request('thread/fork', {
        threadId: agentSessionId,
        beforeTurnId: forkBeforeRef,
        ephemeral: false,
        excludeTurns: true,
      });
      const id = res?.thread?.id;
      if (!id) throw new Error('Codex did not return a thread id for the fork');
      return String(id);
    });
  }

  async start(opts: StartOptions): Promise<AgentSession> {
    const computer = opts.borrowedTool === 'computer.use' ? new BrowserComputer(opts.cwd) : undefined;
    const [bin, ...args] = this.command;
    const child = spawn(bin, args, { cwd: opts.cwd, stdio: ['pipe', 'pipe', 'pipe'], env: sessionEnvironment(this.env, opts.coordinationEnv) });

    const holder: { session: CodexSession | null } = { session: null };
    const peer = new JsonRpcPeer(
      child,
      {
        onNotification: (method, params) => holder.session?.onNotification(method, params),
        onRequest: async (method, params, id) => holder.session?.onRequest(method, params, id),
        onClosed: () => holder.session?.onClosed(),
      },
      this.name,
    );

    try {
      await peer.request('initialize', { clientInfo: { name: 'calli', title: 'Calli', version: '0.1.0' }, capabilities: { experimentalApi: true } });
      peer.notify('initialized', {});

      const mode = opts.permissionMode ? CODEX_MODES[opts.permissionMode] : null;
      // Override only this delegated session; never edit the user's native configuration.
      const toolConfig = opts.borrowedTool === 'web.search' || opts.borrowedTool === 'web.read'
        ? { config: { web_search: 'live' } } : computer ? { dynamicTools: [computerDynamicTool] } : {};
      let threadId: string;
      let nativeModel: string | undefined;
      let nativeEffort: string | null | undefined;
      if (opts.resumeId) {
        const res = await peer.request('thread/resume', {
          threadId: opts.resumeId,
          cwd: opts.cwd,
          excludeTurns: true,
          ...toolConfig,
          ...(mode ? { sandbox: mode.sandbox, approvalPolicy: mode.approvalPolicy, approvalsReviewer: mode.approvalsReviewer } : {}),
        });
        nativeModel = res?.model;
        nativeEffort = res?.reasoningEffort;
        threadId = String(res?.thread?.id ?? opts.resumeId);
      } else {
        const res = await peer.request('thread/start', {
          cwd: opts.cwd,
          ...toolConfig,
          ...(mode ? { sandbox: mode.sandbox, approvalPolicy: mode.approvalPolicy, approvalsReviewer: mode.approvalsReviewer } : {}),
          ...(opts.model ? { model: opts.model } : {}),
        });
        nativeModel = res?.model;
        nativeEffort = res?.reasoningEffort;
        threadId = String(res?.thread?.id ?? '');
        if (!threadId) throw new Error('Codex did not return a thread id');
      }

      const session = new CodexSession(threadId, peer, opts.cwd, nativeModel, nativeEffort, this.name, computer, this.inlineImages);
      holder.session = session;
      session.onGoal = opts.onGoal;
      // An ordinary prompt can also continue a persisted native goal. Older servers omit it.
      // The show also feeds the goal card through onGoal.
      if (opts.resumeId) await session.controlGoal('show').catch(() => undefined);
      // Effort has no place on thread/start; model and mode still ride with the next turn so a
      // resumed thread switches over even where thread/resume kept its old settings.
      if (opts.model) await session.setModel(opts.model);
      if (opts.effort) await session.setEffort(opts.effort);
      if (opts.permissionMode) await session.setPermissionMode(opts.permissionMode);
      if (opts.workMode) await session.setWorkMode(opts.workMode);
      if (opts.speed != null) await session.setSpeed(opts.speed);
      return session;
    } catch (err) {
      peer.close();
      await computer?.close();
      throw err;
    }
  }

  /**
   * Runs one job against a short-lived server that never becomes a thread's session: listing,
   * reading history, forking, deleting. Only skill discovery uses the project's directory;
   * none of these probes starts a thread.
   */
  private async withServer<T>(job: (peer: JsonRpcPeer) => Promise<T>, cwd = tmpdir(), timeoutMs?: number): Promise<T> {
    const [bin, ...args] = this.command;
    const child = spawn(bin, args, { cwd, stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, ...this.env } });
    const peer = new JsonRpcPeer(child, { onNotification: () => undefined, onRequest: async () => undefined }, this.name);
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const run = async () => {
        await peer.request('initialize', { clientInfo: { name: 'calli', title: 'Calli', version: '0.1.0' }, capabilities: { experimentalApi: true } });
        peer.notify('initialized', {});
        return job(peer);
      };
      return await (timeoutMs === undefined ? run() : Promise.race([
        run(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error('读取 Codex 会话超时，请重试。')), timeoutMs);
        }),
      ]));
    } finally {
      clearTimeout(timer);
      peer.close();
    }
  }
}

/**
 * A rollout another account can go on with: reasoning items whose content is sealed for the account
 * that wrote them are dropped. Throws for a native compaction, whose sealed summary stands in for
 * the earlier conversation and could not be dropped without losing it.
 */
export function portableRollout(text: string): string {
  const out: string[] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    let record: any;
    try { record = JSON.parse(line); } catch { out.push(line); continue; }
    const payload = record?.payload;
    if (record?.type === 'compacted' && line.includes('"encrypted_content"')
      || record?.type === 'response_item' && payload?.type !== 'reasoning' && payload?.encrypted_content) {
      throw new Error('this conversation was compacted natively; only its own account can read the summary');
    }
    if (record?.type === 'response_item' && payload?.type === 'reasoning' && payload.encrypted_content) continue;
    out.push(line);
  }
  return out.join('\n') + '\n';
}
