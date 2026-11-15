import { piUsage } from './usage.js';
import { cliDiagnostic, probeWithDeadline } from './diagnostics.js';
import { isCompaction, trackCompaction } from './compaction.js';
import { PlanReply } from './plan-reply.js';
import { sessionEnvironment } from './types.js';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { closeSync, existsSync, openSync, readdirSync, readFileSync, readSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import type { EmittedEvent } from '../events.js';
import { imagePathNote } from '../attachments.js';
import { commandExists, textOf } from '../util.js';
import { PI_ICON } from './icons.js';
import { configDirectory, findSessionFile, quoteArg } from './session-location.js';
import { piSystemPromptLine, scanJsonl } from './system-prompt.js';
import { installJsonMcpServer, jsonMcpServers, removeJsonMcpServer } from '../mcp-format.js';
import type {
  AgentAdapter,
  AgentSession,
  AgentSessionSummary,
  EffortLevel,
  HistoryTurn,
  McpServerInfo,
  ModelOption,
  SkillDir,
  SlashCommand,
  StartOptions,
  SystemPrompt,
  TurnHooks,
  TurnInput,
  TurnResult,
  WorkMode,
} from './types.js';

const PI_EFFORTS: EffortLevel[] = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

/** Commands and MCP status can change when the user edits pi's config. */
const PROBE_TTL_MS = 60_000;
const MODEL_TTL_MS = 10 * 60_000;

/** How long `pi mcp list` may take to connect every server before the probe gives up. */
const MCP_LIST_TIMEOUT_MS = 30_000;

/**
 * Thinking levels a pi model accepts, by pi's own rule: a level mapped to null is unsupported,
 * and xhigh/max count only when the model maps them explicitly. Models without reasoning have none.
 */
export function piEfforts(model: any): EffortLevel[] {
  if (!model?.reasoning) return [];
  return PI_EFFORTS.filter((level) => {
    const mapped = model.thinkingLevelMap?.[level];
    if (mapped === null) return false;
    return level === 'xhigh' || level === 'max' ? mapped !== undefined : true;
  });
}

const PI_TOOL_KIND: Record<string, string> = {
  bash: 'execute',
  read: 'read',
  ls: 'read',
  edit: 'edit',
  write: 'edit',
  grep: 'search',
  find: 'search',
};

/** A short line for the step list, such as the command or the file being touched. */
function piToolTitle(name: string, args: any): string {
  switch (name) {
    case 'bash':
      return String(args?.command ?? 'bash').split('\n')[0];
    case 'read':
    case 'edit':
    case 'write':
      // The row already gets its verb from the kind ("读取了 …"), so the title is just the path;
      // keeping the English verb here read as "读取了 Read x".
      return String(args?.path ?? '').trim();
    default: {
      const target = args?.pattern ?? args?.path ?? args?.query;
      return target ? `${name} ${target}` : name;
    }
  }
}

/** File changes of an edit or write call, in the same shape Claude's adapter reports them. */
function piDiffs(name: string, args: any): Array<{ type: 'diff'; path: string; oldText: string | null; newText: string }> {
  if (typeof args?.path !== 'string') return [];
  if (name === 'write' && typeof args.content === 'string') {
    return [{ type: 'diff', path: args.path, oldText: null, newText: args.content }];
  }
  if (name === 'edit') {
    const edits = Array.isArray(args.edits) ? args.edits : typeof args.newText === 'string' ? [args] : [];
    return edits
      .filter((e: any) => typeof e?.newText === 'string')
      .map((e: any) => ({ type: 'diff' as const, path: args.path, oldText: String(e.oldText ?? ''), newText: e.newText }));
  }
  return [];
}

/** How much of a session file is read to find its title. The first user message is near the top. */
const SESSION_HEAD_BYTES = 512 * 1024;

/**
 * Reads what the session list needs from the start of a pi session file: the header line, and a
 * title taken from the session's name if it has one, else from the first thing the user said.
 */
export function summarisePiSession(headText: string, updatedAt: string | null): AgentSessionSummary | null {
  const lines = headText.split('\n');
  let header: any;
  try {
    header = JSON.parse(lines[0]);
  } catch {
    return null;
  }
  if (header?.type !== 'session' || !header.id) return null;

  let name = '';
  let firstUserText = '';
  for (const line of lines.slice(1)) {
    // Most lines are long tool output; only two kinds matter, so skip the rest without parsing.
    const isName = line.includes('"type":"session_info"');
    const isUser = !firstUserText && line.includes('"role":"user"');
    if (!isName && !isUser) continue;
    let entry: any;
    try {
      entry = JSON.parse(line);
    } catch {
      continue; // the last line may be cut off by the read limit
    }
    if (entry.type === 'session_info' && typeof entry.name === 'string') name = entry.name; // the latest name wins
    if (entry.type === 'message' && entry.message?.role === 'user' && !firstUserText) {
      firstUserText = textOf(entry.message.content).trim();
    }
  }
  return {
    id: String(header.id),
    cwd: String(header.cwd ?? ''),
    title: (name || firstUserText.split('\n')[0] || '').slice(0, 80),
    updatedAt,
  };
}

/**
 * pi forks from a user message: the copy holds everything before it. So the conversation as it
 * stood when a turn ended is a fork from the next turn's message, and the last turn has no such
 * point (its copy is a clone). `forkMessages` is what `get_fork_messages` lists, in order; a turn
 * is matched to its message by text, so a message pi lists that is not a turn here is passed over.
 * Each turn also gets the message that starts it (`forkBeforeRef`): forking from that message copies
 * what came before it, which is what editing it needs.
 */
export function withPiForkRefs(turns: HistoryTurn[], forkMessages: any[]): HistoryTurn[] {
  const entryOf: Array<string | undefined> = [];
  let next = 0;
  for (const turn of turns) {
    let at = next;
    while (at < forkMessages.length && String(forkMessages[at]?.text ?? '').trim() !== turn.text.trim()) at++;
    if (at < forkMessages.length) {
      entryOf.push(typeof forkMessages[at]?.entryId === 'string' ? forkMessages[at].entryId : undefined);
      next = at + 1;
    } else {
      entryOf.push(undefined);
    }
  }
  return turns.map((turn, i) => ({
    ...turn,
    ...(entryOf[i + 1] ? { forkRef: entryOf[i + 1] } : {}),
    ...(entryOf[i] ? { forkBeforeRef: entryOf[i] } : {}),
  }));
}

/** Turns the messages pi returns for a session into past turns. A user message starts a turn. */
export function turnsFromPiMessages(messages: any[]): HistoryTurn[] {
  const turns: HistoryTurn[] = [];
  let current: HistoryTurn | null = null;
  for (const m of messages ?? []) {
    if (m?.role === 'user') {
      const blocks = Array.isArray(m.content) ? m.content : [];
      current = {
        text: typeof m.content === 'string' ? m.content : textOf(blocks.filter((b: any) => b?.type === 'text')),
        images: blocks
          .filter((b: any) => b?.type === 'image' && typeof b.data === 'string')
          .map((b: any) => ({ data: b.data, mimeType: String(b.mimeType ?? 'image/png') })),
        events: [],
      };
      turns.push(current);
      continue;
    }
    if (!current) continue; // system messages and anything else before the first user message
    if (m?.role === 'assistant') {
      for (const block of Array.isArray(m.content) ? m.content : []) {
        if (block?.type === 'thinking' && block.thinking) {
          current.events.push({ type: 'reasoning.delta', data: { text: String(block.thinking) } });
        } else if (block?.type === 'text' && block.text) {
          current.events.push({ type: 'message.delta', data: { text: String(block.text) } });
        } else if (block?.type === 'toolCall') {
          const start = mapPiEvent({ type: 'tool_execution_start', toolCallId: block.id, toolName: block.name, args: block.arguments });
          if (start) current.events.push(start);
        }
      }
    } else if (m?.role === 'toolResult') {
      const end = mapPiEvent({ type: 'tool_execution_end', toolCallId: m.toolCallId, result: { content: m.content }, isError: m.isError });
      if (end) current.events.push(end);
    }
  }
  return turns;
}

/**
 * Maps one pi RPC event record to a unified event. Pure, so it can be tested without a process.
 * Returns null for records the UI does not render. Turn completion is handled by the session.
 */
export function mapPiEvent(record: any): EmittedEvent | null {
  switch (record?.type) {
    case 'message_update': {
      const ev = record.assistantMessageEvent;
      if (ev?.type === 'text_delta') return { type: 'message.delta', data: { text: ev.delta ?? '' } };
      if (ev?.type === 'thinking_delta') return { type: 'reasoning.delta', data: { text: ev.delta ?? '' } };
      return null;
    }
    case 'tool_execution_start': {
      const name = String(record.toolName ?? '');
      const diffs = piDiffs(name, record.args);
      return {
        type: 'tool.start',
        data: {
          id: record.toolCallId,
          title: piToolTitle(name, record.args),
          kind: PI_TOOL_KIND[name] ?? 'other',
          status: 'in_progress',
          // The name the model called, kept for the activity page (`mcp__server__tool` for MCP).
          ...(name ? { tool: name } : {}),
          input: record.args,
          ...(diffs.length ? { content: diffs } : {}),
        },
      };
    }
    case 'tool_execution_update':
      return {
        type: 'tool.update',
        data: { id: record.toolCallId, status: 'in_progress', output: textOf(record.partialResult?.content) },
      };
    case 'tool_execution_end':
      return {
        type: 'tool.end',
        data: {
          id: record.toolCallId,
          status: record.isError ? 'failed' : 'completed',
          output: textOf(record.result?.content),
        },
      };
    default:
      return null;
  }
}

/** Context window use and cost, from pi's session statistics. Null when pi does not report a window. */
export function mapPiStats(stats: any): EmittedEvent | null {
  const usage = stats?.contextUsage;
  if (typeof usage?.contextWindow !== 'number' || !Number.isFinite(usage.contextWindow) || usage.contextWindow <= 0) return null;
  // Pi explicitly reports null after compaction until a new assistant response measures it.
  if (usage.tokens !== null && (typeof usage.tokens !== 'number' || !Number.isFinite(usage.tokens) || usage.tokens < 0)) return null;
  const cost = typeof stats.cost === 'number' && Number.isFinite(stats.cost) && stats.cost >= 0 ? { amount: stats.cost, currency: 'USD' } : null;
  return { type: 'usage.updated', data: { used: usage.tokens, size: usage.contextWindow, ...(cost ? { cost } : {}) } };
}

/**
 * Built-in pi commands that its RPC mode offers as requests instead of slash commands.
 * `get_commands` leaves built-ins out, so they are listed here and answered by the session.
 */
export const PI_LOCAL_COMMANDS: SlashCommand[] = [
  { name: 'compact', description: 'Compact the current context, optionally with custom instructions', hint: '[instructions]', kind: 'command' },
  { name: 'session', description: 'Show current session information and statistics', kind: 'command' },
];

const tokens = (n: unknown) => {
  const v = Number(n) || 0;
  return v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v);
};

/** pi's answer to `compact`, as a message. */
export function formatPiCompact(data: any): string {
  const before = tokens(data?.tokensBefore);
  const after = data?.estimatedTokensAfter !== undefined ? tokens(data.estimatedTokensAfter) : null;
  return after ? `已压缩上下文：${before} → 约 ${after} tokens。` : `已压缩上下文（压缩前 ${before} tokens）。`;
}

/** pi's session statistics, as a small table. */
export function formatPiStats(stats: any): string {
  const t = stats?.tokens ?? {};
  const rows: Array<[string, string]> = [
    ['消息', `你 ${stats?.userMessages ?? 0} · 助手 ${stats?.assistantMessages ?? 0} · 工具调用 ${stats?.toolCalls ?? 0}`],
    ['Token', `输入 ${tokens(t.input)} · 输出 ${tokens(t.output)} · 缓存读 ${tokens(t.cacheRead)} · 缓存写 ${tokens(t.cacheWrite)}`],
  ];
  if (typeof stats?.cost === 'number') rows.push(['费用', `$${stats.cost.toFixed(4)}`]);
  const ctx = stats?.contextUsage;
  if (mapPiStats(stats)) rows.push(['上下文', ctx.tokens === null ? `待下一次响应更新 / ${tokens(ctx.contextWindow)}` : `${tokens(ctx.tokens)} / ${tokens(ctx.contextWindow)}（${Math.round((ctx.tokens / ctx.contextWindow) * 1000) / 10}%）`]);
  if (stats?.sessionId) rows.push(['会话 ID', `\`${stats.sessionId}\``]);
  return ['| | |', '|---|---|', ...rows.map(([k, v]) => `| ${k} | ${v} |`)].join('\n');
}

/**
 * The pi extension that lets a model without image input look at pictures: it adds a describe_image
 * tool that hands an image file to a vision model. It is recognised by the command it registers.
 */
export const PI_VISION_TOOL = 'describe_image';
const PI_VISION_EXTENSION_COMMAND = 'vision';

/**
 * The zhipu vision MCP server (`@z_ai/mcp-server`): like the extension above, it gives a text-only
 * model tools that hand a picture to a vision model, but the picture goes through zhipu's API with
 * its own key. Any of its tools grants sight, and they take a file path, which is how the hub sends
 * pictures anyway. Detected through `pi mcp list --json`.
 */
const PI_VISION_MCP_SERVER = 'zai-mcp-server';
// The docs call it image_analysis; version 0.1.5 actually ships analyze_image. This string is only
// what the UI shows when a model sees pictures through the server, so it names the general tool.
export const PI_MCP_VISION_TOOL = 'analyze_image';

/** A per-launch marker also identifies MCP servers that detach or outlive Pi on Linux. */
async function stopPiProcess(child: import('node:child_process').ChildProcess, owner: string): Promise<void> {
  if (!child.pid) return;
  const pid = child.pid;
  const signal = (name: NodeJS.Signals) => {
    if (process.platform === 'linux') {
      for (const entry of readdirSync('/proc')) {
        if (!/^\d+$/.test(entry)) continue;
        try {
          const env = readFileSync('/proc/' + entry + '/environ', 'utf8');
          if (env.split('\0').includes('CALLI_PI_PROCESS_OWNER=' + owner)) process.kill(Number(entry), name);
        } catch (error) {
          // Other users' processes and processes which exited during the scan are expected.
          if (!['ENOENT', 'ESRCH', 'EACCES', 'EPERM'].includes((error as NodeJS.ErrnoException).code ?? '')) throw error;
        }
      }
      return;
    }
    if (process.platform === 'win32') {
      if (child.exitCode === null && child.signalCode === null) child.kill(name);
      return;
    }
    try { process.kill(-pid, name); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ESRCH') throw error; }
  };
  signal('SIGTERM');
  await new Promise(resolve => setTimeout(resolve, 250));
  signal('SIGKILL');
}

/**
 * Runs `pi mcp list --json` and returns every tool of every connected server, qualified as the
 * model sees them: `mcp__<server>__<tool>`. A server that never finishes the MCP handshake makes
 * the command hang, so it runs under a timeout, and any failure is an empty list — which only
 * means no MCP vision, never a broken hub.
 */
function mcpToolNames(command: string[], env: Record<string, string>): Promise<string[]> {
  const owner = randomUUID();
  const child = spawn(command[0], [...command.slice(1), 'mcp', 'list', '--json'], {
    cwd: process.cwd(),
    stdio: ['ignore', 'pipe', 'ignore'],
    detached: process.platform !== 'win32',
    env: { ...process.env, ...env, CALLI_PI_PROCESS_OWNER: owner },
  });
  return new Promise((resolve, reject) => {
    let out = '';
    let settled = false;
    const done = (names: string[]) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      void stopPiProcess(child, owner).then(() => resolve(names), reject);
    };
    const timer = setTimeout(() => done([]), MCP_LIST_TIMEOUT_MS);
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => {
      out += chunk;
    });
    child.on('error', () => done([]));
    child.on('exit', () => {
      try {
        const parsed = JSON.parse(out);
        done(
          (parsed?.servers ?? [])
            .filter((s: any) => s?.enabled && s?.state === 'connected')
            .flatMap((s: any) => (Array.isArray(s?.tools) ? s.tools : []).map((t: unknown) => `mcp__${s.name}__${t}`)),
        );
      } catch {
        done([]);
      }
    });
  });
}

class PiSession implements AgentSession {
  agentSessionId = '';
  private workMode: WorkMode = 'execute';
  private clearPlanInstruction = false;
  private nextId = 1;
  private usageId = 0;
  private requestStarted: number | null = null;
  private firstTokenMs: number | undefined;
  private readonly pending = new Map<string, { resolve(v: any): void; reject(e: Error): void }>();
  private hooks: TurnHooks | null = null;
  private turn: { resolve(r: TurnResult): void; error?: string; plan?: PlanReply; submitted?: boolean } | null = null;
  private buffer = '';
  private exited = false;
  private closing: Promise<void> | null = null;
  private compactionSequence = 0;
  private activeCompaction: string | null = null;

  constructor(
    private readonly child: ChildProcessWithoutNullStreams,
    private readonly label: string,
    private readonly owner: string,
  ) {
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => this.onData(chunk));
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk: string) => {
      for (const line of chunk.split('\n')) if (line.trim()) console.error(`[${this.label}] ${line}`);
    });
    child.on('exit', (code, signal) => this.onExit(code, signal));
    child.on('error', (err) => this.onExit(null, String(err)));
  }

  private static spawn(
    command: string[],
    extraArgs: string[],
    cwd: string,
    label: string,
    env: Record<string, string> = {},
  ): PiSession {
    const [bin, ...base] = command;
    const owner = randomUUID();
    const child = spawn(bin, [...base, '--mode', 'rpc', ...extraArgs], {
      cwd,
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: process.platform !== 'win32',
      env: { ...process.env, ...env, CALLI_PI_PROCESS_OWNER: owner },
    });
    return new PiSession(child, label, owner);
  }

  /** A session for a thread. pi saves it, so it can be resumed later and from the terminal. */
  static async open(
    command: string[],
    opts: StartOptions,
    label: string,
    env: Record<string, string> = {},
    /** Instead of resuming `opts.resumeId`: the arguments that say which session this pi is on. */
    sessionArgs: string[] = opts.resumeId ? ['--session', opts.resumeId] : [],
  ): Promise<PiSession> {
    const session = PiSession.spawn(command, sessionArgs, opts.cwd, label, sessionEnvironment(env, opts.coordinationEnv) as Record<string, string>);
    try {
      const state = await session.request('get_state', {});
      const sessionId = String(state?.sessionId ?? '');
      if (!sessionId) throw new Error('pi did not report a session id');
      session.agentSessionId = sessionId;
      session.workMode = opts.workMode ?? 'execute';
      // A resumed conversation may still contain a previous planning instruction.
      session.clearPlanInstruction = !!opts.resumeId && session.workMode === 'execute';
      if (opts.model) await session.setModel(opts.model);
      if (opts.effort) await session.setEffort(opts.effort);
      return session;
    } catch (err) {
      await session.close();
      throw err;
    }
  }

  /** A throwaway process for asking pi questions. `--no-session` keeps it out of pi's session list. */
  static async probe<T>(
    command: string[],
    cwd: string,
    label: string,
    env: Record<string, string>,
    ask: (s: PiSession) => Promise<T>,
    extraArgs: string[] = [],
  ): Promise<T> {
    const session = PiSession.spawn(command, ['--no-session', ...extraArgs], cwd, label, env);
    try {
      return await probeWithDeadline(() => ask(session), () => session.close());
    } finally {
      await session.close();
    }
  }

  request(type: string, payload: Record<string, unknown>): Promise<any> {
    if (this.exited) return Promise.reject(new Error(`${this.label} is not running`));
    const id = `req-${this.nextId++}`;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.child.stdin.write(JSON.stringify({ id, type, ...payload }) + '\n');
    });
  }

  private onData(chunk: string): void {
    this.buffer += chunk;
    let idx: number;
    while ((idx = this.buffer.indexOf('\n')) >= 0) {
      let line = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 1);
      if (line.endsWith('\r')) line = line.slice(0, -1);
      if (line.trim()) this.onRecord(line);
    }
  }

  private onRecord(line: string): void {
    let record: any;
    try {
      record = JSON.parse(line);
    } catch {
      console.error(`[${this.label}] non-JSON line on stdout: ${line.slice(0, 200)}`);
      return;
    }

    if (record.type === 'response') {
      const p = record.id ? this.pending.get(record.id) : undefined;
      if (p) {
        this.pending.delete(record.id);
        if (record.success === false) p.reject(new Error(record.error ?? 'pi command failed'));
        else p.resolve(record.data);
      } else if (record.success === false && this.turn) {
        this.turn.error = record.error ?? 'pi command failed';
      }
      if (record.command === 'prompt' && record.success && record.data?.disposition === 'handled' && this.turn) {
        // Handled by an extension or input hook: no run starts, so there is no agent_settled.
        this.finishTurn('end_turn');
      }
      return;
    }

    if (record.type === 'message_start' && record.message?.role === 'assistant') {
      this.hooks?.emit({ type: 'turn.phase', data: { phase: 'model_request' } });
      this.requestStarted = typeof record.message.timestamp === 'number' && record.message.timestamp > 0 && record.message.timestamp <= Date.now() ? record.message.timestamp : null;
      this.firstTokenMs = undefined;
    }
    if (record.type === 'message_update' && this.requestStarted !== null && this.firstTokenMs === undefined
      && ['text_delta', 'thinking_delta', 'toolcall_delta'].includes(record.assistantMessageEvent?.type)) {
      this.firstTokenMs = Date.now() - this.requestStarted;
    }
    if (record.type === 'message_end' && this.hooks) {
      const sample = piUsage(record.message, `message-${++this.usageId}`, this.firstTokenMs);
      if (sample) this.hooks.emit({ type: 'usage.sample', data: { ...sample } });
      this.requestStarted = null;
      this.firstTokenMs = undefined;
    }

    if (record.type === 'message_end' && record.message?.role === 'assistant' && record.message?.stopReason === 'error') {
      if (this.turn) this.turn.error = record.message.errorMessage ?? 'model error';
    }

    if (record.type === 'agent_settled') {
      const stop = record.aborted ? 'cancelled' : this.turn?.error ? 'error' : 'end_turn';
      // The turn is over for pi; ask how full the context is before reporting it done.
      void this.reportUsage().finally(() => this.finishTurn(stop));
      return;
    }

    // Current Pi also reports manual compaction; older versions use the auto_ prefix.
    if (['compaction_start', 'compaction_end', 'auto_compaction_start', 'auto_compaction_end'].includes(record.type)) {
      if (this.compacting || !this.hooks) return;
      const starting = record.type.endsWith('_start');
      const id = this.activeCompaction ?? `pi-compaction-${++this.compactionSequence}`;
      this.hooks.emit({ type: 'context.compaction', data: {
        id, automatic: record.reason !== 'manual',
        status: starting ? 'in_progress' : record.aborted ? 'cancelled' : record.result ? 'completed' : record.errorMessage ? 'failed' : 'skipped',
      } });
      this.activeCompaction = starting ? id : null;
      return;
    }
    const ev = mapPiEvent(record);
    if (!ev || !this.hooks) return;
    const plan = this.turn?.plan;
    if (plan && ev.type === 'message.delta') return plan.add(String(ev.data.text ?? ''));
    if (plan && ev.type === 'tool.start') plan.aside();
    this.hooks.emit(ev);
  }

  private async reportUsage(): Promise<void> {
    if (!this.hooks || this.exited) return;
    try {
      const ev = mapPiStats(await this.request('get_session_stats', {}));
      if (ev) this.hooks?.emit(ev);
    } catch {
      // A pi without this command: there is simply no usage to show.
    }
  }

  private finishTurn(stopReason: string): void {
    const turn = this.turn;
    if (!turn) return;
    turn.plan?.finish(stopReason === 'end_turn');
    this.turn = null;
    this.activeCompaction = null;
    this.hooks = null;
    turn.resolve({ stopReason, error: turn.error });
  }

  private onExit(code: number | null, signal: string | null | NodeJS.Signals): void {
    this.exited = true;
    void this.close().catch(error => console.error('[pi] process cleanup failed:', error));
    const err = new Error(`${this.label} exited (code=${code}, signal=${signal})`);
    for (const [, p] of this.pending) p.reject(err);
    this.pending.clear();
    if (this.turn) {
      this.turn.error = err.message;
      this.finishTurn('error');
    }
  }

  /** Answers the built-in commands pi takes as requests. Null when `text` is not one of them. */
  private async local(text: string, hooks: TurnHooks): Promise<TurnResult | null> {
    const m = /^\/(compact|session)(?:\s+([\s\S]*))?$/.exec(text.trim());
    if (!m) return null;
    try {
      if (m[1] === 'session') {
        hooks.emit({ type: 'message.delta', data: { text: formatPiStats(await this.request('get_session_stats', {})) } });
      } else {
        const instructions = m[2]?.trim();
        hooks.emit({ type: 'message.delta', data: { text: formatPiCompact(await this.request('compact', instructions ? { customInstructions: instructions } : {})) } });
        const usage = mapPiStats(await this.request('get_session_stats', {}).catch(() => null));
        if (usage) hooks.emit(usage);
      }
      return { stopReason: 'end_turn' };
    } catch (err) {
      if (this.compactCancelRequested) return { stopReason: 'cancelled' };
      return { stopReason: 'error', error: err instanceof Error ? err.message : String(err) };
    }
  }

  private compacting = false;
  private compactCancelRequested = false;

  async prompt(input: TurnInput, hooks: TurnHooks): Promise<TurnResult> {
    if (this.turn || this.compacting) throw new Error('a turn is already running');
    if (input.images.length === 0) {
      if (isCompaction(input.text)) {
        this.compacting = true;
        this.compactCancelRequested = false;
        try { return await trackCompaction(hooks, async () => (await this.local(input.text, hooks))!); }
        finally { this.compacting = false; this.compactCancelRequested = false; }
      }
      // Ordinary prompts must register their turn before yielding, so an immediate
      // stop cannot fall between mode application and the prompt being sent.
      if (/^\/session(?:\s|$)/.test(input.text.trim())) return (await this.local(input.text, hooks))!;
    }
    this.hooks = hooks;
    let resolveTurn!: (r: TurnResult) => void;
    const result = new Promise<TurnResult>((resolve) => {
      resolveTurn = resolve;
    });
    const turn: NonNullable<PiSession['turn']> = { resolve: resolveTurn };
    this.turn = turn;
    try {
      // A model that cannot see would have the pictures dropped. It gets their paths instead, for its
      // describe_image tool; the hub only lets such a message through when that tool is installed.
      let native = true;
      if (input.images.length) {
        hooks.emit({ type: 'turn.phase', data: { phase: 'image_check' } });
        // Ask the live session, avoiding a second process and its extension/MCP startup.
        const state = await Promise.race([this.request('get_state', {}), result.then(() => null)]);
        if (this.turn !== turn) return result;
        native = !Array.isArray(state?.model?.input) || state.model.input.includes('image');
        hooks.emit({ type: 'turn.phase', data: { phase: 'image_ready', native } });
      }
      let message = native ? input.text : input.text + imagePathNote(input.images);
      // Keep extension commands and prompt templates at the start of the message.
      // This is a conversational instruction, not a tool permission boundary.
      if (!input.text.trimStart().startsWith('/')) {
        if (this.workMode === 'plan') {
          // pi has no plan of its own: what it answers with is the plan.
          this.turn.plan = new PlanReply((ev) => hooks.emit(ev), 'plan');
          message = '[Hub work mode: plan]\n先分析需求并给出简短的设计方案和执行步骤。这一轮只讨论方案，不修改文件、不执行实施操作；等用户要求开始执行后再做。\n\n' + message;
        } else if (this.clearPlanInstruction) {
          message = '[Hub work mode: execute]\n已结束计划模式，按用户当前要求执行；此前只讨论、不实施的限制已解除。\n\n' + message;
        }
      }
      turn.submitted = true;
      hooks.emit({ type: 'turn.phase', data: { phase: 'prompt_sent' } });
      await this.request('prompt', {
        message,
        ...(native && input.images.length
          ? { images: input.images.map((img) => ({ type: 'image', data: img.data, mimeType: img.mimeType })) }
          : {}),
      });
      if (!input.text.trimStart().startsWith('/')) this.clearPlanInstruction = false;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (this.turn === turn) {
        turn.error = msg;
        this.finishTurn('error');
      }
    }
    return result;
  }

  /** Model ids are `provider/id`, matching what models() returns. */
  async setModel(model: string): Promise<boolean> {
    const slash = model.indexOf('/');
    if (slash < 0) throw new Error(`pi model must be provider/id, got: ${model}`);
    await this.request('set_model', { provider: model.slice(0, slash), modelId: model.slice(slash + 1) });
    return true;
  }

  async setEffort(effort: EffortLevel): Promise<boolean> {
    await this.request('set_thinking_level', { level: effort });
    return true;
  }

  async setWorkMode(mode: WorkMode): Promise<void> {
    if (this.turn || this.compacting) throw new Error('a turn is already running');
    if (this.workMode === 'plan' && mode === 'execute') this.clearPlanInstruction = true;
    this.workMode = mode;
  }

  async cancel(): Promise<void> {
    if (!this.turn && !this.compacting) return;
    if (this.turn && !this.turn.submitted) { this.finishTurn('cancelled'); return; }
    if (this.compacting) this.compactCancelRequested = true;
    await this.request('abort', {}).catch(() => undefined);
  }

  async close(): Promise<void> {
    if (!this.closing) {
      this.child.stdin.end();
      this.closing = stopPiProcess(this.child, this.owner);
    }
    await this.closing;
  }
}

interface Cached<T> {
  at: number;
  value: Promise<T>;
}

export const PI_METADATA = {
  adapter: 'pi' as const,
  label: 'pi',
  icon: PI_ICON,
  installGuide: 'npm install -g @earendil-works/pi-coding-agent',
};

export class PiRpcAdapter implements AgentAdapter {
  readonly id = 'pi';
  readonly label = PI_METADATA.label;
  readonly icon = PI_METADATA.icon;
  readonly installGuide = PI_METADATA.installGuide;
  // pi has no approval flow, so there is nothing to choose.
  readonly capabilities = {
    workModes: ['execute', 'plan'] as const,
    coordination: true,
    resume: true,
    modelSwitch: true,
    approvals: false,
    permissionModes: [] as const,
    // The widest set. Each model lists the levels it really takes in ModelOption.efforts.
    effortLevels: PI_EFFORTS,
    images: true,
    // pi has no command for this, and removing its session files ourselves would be meddling.
    deleteSession: false,
    history: true,
    fork: true,
    forkAt: true,
    editMessage: true,
    multiAccount: false,
    // `--fork` copies a session into the directory pi is started in.
    forkInto: true,
    // pi has no plan of its own to read: it runs on whatever model account it was given.
    quota: false,
    compact: true,
    systemPrompt: true,
  };

  private modelCache: { at: number; value: ModelOption[] } | null = null;
  private modelLoad: Promise<ModelOption[]> | null = null;
  private modelRetryAt = 0;
  onModelsChanged?: (models: ModelOption[]) => void;
  private mcpVision: boolean | undefined;
  private mcpCache: Cached<string[]> | null = null;
  private readonly commandCache = new Map<string, Cached<SlashCommand[]>>();

  constructor(
    private readonly command: string[],
    /** Extra environment for this agent instance, such as a second account's config directory. */
    private readonly env: Record<string, string> = {},
  ) {}

  diagnose() { return cliDiagnostic(this.command, this.env, 'pi RPC'); }

  async available(): Promise<boolean> {
    return commandExists(this.command[0]);
  }

  /** Reuses a probe result for a short while; a failed probe is not kept. */
  private cached<T>(current: Cached<T> | null | undefined, load: () => Promise<T>, store: (c: Cached<T> | null) => void): Promise<T> {
    if (current && Date.now() - current.at < PROBE_TTL_MS) return current.value;
    const entry: Cached<T> = { at: Date.now(), value: load() };
    store(entry);
    entry.value.catch(() => store(null));
    return entry.value;
  }

  /**
   * pi's configured models, with what each one supports. The model pi would use by default is
   * marked, so the UI can show what "default" means and which settings apply to it.
   */
  models(refresh = false): Promise<ModelOption[]> {
    if (refresh || !this.modelCache) return this.loadModels().then(models => this.withMcpVision(models));
    if (Date.now() - this.modelCache.at >= MODEL_TTL_MS && Date.now() >= this.modelRetryAt) {
      // A stale list remains usable while the next probe runs, including when that probe fails.
      void this.loadModels().catch(() => {});
    }
    return Promise.resolve(this.withMcpVision(this.modelCache.value));
  }

  private loadModels(): Promise<ModelOption[]> {
    if (this.modelLoad) return this.modelLoad;
    // MCP connects separately and must never hold up the model picker. Preserve a known
    // result during refresh; the native vision extension still takes precedence below.
    void this.probeMcpVision();
    this.modelLoad = PiSession.probe(this.command, process.cwd(), 'pi', this.env, async session => {
      const [list, state, commands] = await Promise.all([
        session.request('get_available_models', {}),
        session.request('get_state', {}),
        session.request('get_commands', {}).catch(() => null),
      ]);
      const current = state?.model ? `${state.model.provider}/${state.model.id}` : null;
      const vision = (commands?.commands ?? []).some(
        (c: any) => String(c.name).replace(/^\//, '') === PI_VISION_EXTENSION_COMMAND && c.source === 'extension',
      );
      return (list?.models ?? []).map((model: any): ModelOption => {
        const id = `${model.provider}/${model.id}`;
        const images = Array.isArray(model.input) ? model.input.includes('image') : undefined;
        return {
          id, label: model.name ?? model.id, description: model.provider, efforts: piEfforts(model), images,
          ...(images === false && vision ? { imagesVia: PI_VISION_TOOL } : {}),
          ...(id === current ? { isDefault: true } : {}),
        };
      });
    }, ['--no-mcp']).then(models => {
      const previous = this.modelCache;
      this.modelCache = { at: Date.now(), value: models };
      this.modelRetryAt = 0;
      if (previous) this.onModelsChanged?.(this.withMcpVision(models));
      return models;
    }, error => {
      this.modelRetryAt = Date.now() + PROBE_TTL_MS;
      throw error;
    }).finally(() => { this.modelLoad = null; });
    return this.modelLoad;
  }

  private probeMcpVision(): Promise<string[]> {
    return this.cached(this.mcpCache, async () => {
      const tools = await mcpToolNames(this.command, this.env);
      const supported = tools.some(t => t.startsWith(`mcp__${PI_VISION_MCP_SERVER}__`));
      const changed = this.mcpVision !== supported;
      this.mcpVision = supported;
      if (changed && this.modelCache) this.onModelsChanged?.(this.withMcpVision(this.modelCache.value));
      return tools;
    }, cache => { this.mcpCache = cache; });
  }

  async modelForImages(model: string | null): Promise<ModelOption | undefined> {
    await this.models();
    const select = (models: ModelOption[]) => model === null ? models.find(m => m.isDefault) : models.find(m => m.id === model);
    const native = select(this.modelCache!.value);
    // Native vision and the vision extension do not depend on an MCP connection.
    if (native?.images !== false || native.imagesVia) return native;
    await this.probeMcpVision();
    return select(this.withMcpVision(this.modelCache!.value));
  }

  private withMcpVision(models: ModelOption[]): ModelOption[] {
    return models.map(model => {
      if (model.images !== false || model.imagesVia) return model;
      // Pending detection is unknown, not proof that the model has no image route. The live
      // session checks native image support before sending images or their file paths.
      if (this.mcpVision === undefined) return { ...model, images: undefined };
      return this.mcpVision ? { ...model, imagesVia: PI_MCP_VISION_TOOL } : model;
    });
  }

  /** pi's commands for a directory: extension commands, prompt templates and skills. */
  commands(cwd?: string): Promise<SlashCommand[]> {
    const dir = cwd ?? process.cwd();
    return this.cached(
      this.commandCache.get(dir),
      () =>
        PiSession.probe(
          this.command,
          dir,
          'pi',
          this.env,
          async (s) => {
          const data = await s.request('get_commands', {});
          const reported = (data?.commands ?? []).map((c: any): SlashCommand => ({
            name: String(c.name).replace(/^\//, ''),
            description: c.description ?? '',
            kind: c.source === 'skill' ? 'skill' : c.source === 'prompt' ? 'prompt' : 'command',
          }));
          const local = new Set(PI_LOCAL_COMMANDS.map((c) => c.name));
          return [...PI_LOCAL_COMMANDS, ...reported.filter((c: SlashCommand) => !local.has(c.name))];
        }),
      (c) => {
        if (c) this.commandCache.set(dir, c);
        else this.commandCache.delete(dir);
      },
    );
  }

  resumeCommand(agentSessionId: string): string {
    return `pi --session ${agentSessionId}`;
  }

  async sessionLocation(agentSessionId: string | null, cwd: string) {
    const configDir = configDirectory(this.env, 'PI_CODING_AGENT_DIR', ['.pi', 'agent'], cwd);
    const sessionPath = await findSessionFile(join(configDir, 'sessions'), agentSessionId, 1, (name, id) => name.endsWith(`_${id}.jsonl`));
    return { configDir, sessionPath, ...(agentSessionId ? { resumeCommand: `env PI_CODING_AGENT_DIR=${quoteArg(configDir)} pi --session ${quoteArg(sessionPath ?? agentSessionId)}` } : {}) };
  }

  /**
   * Another instance's session (another key or provider), copied under a new id into this
   * instance's sessions folder for the same directory. pi keeps the whole conversation itself and
   * can go on with it on any model; only the header names the session.
   */
  async importSession(sourcePath: string): Promise<string> {
    const text = await readFile(sourcePath, 'utf8');
    const end = text.indexOf('\n');
    const header = JSON.parse(end < 0 ? text : text.slice(0, end));
    if (header?.type !== 'session' || typeof header.id !== 'string') throw new Error('not a pi session file');
    const id = randomUUID();
    const dir = join(configDirectory(this.env, 'PI_CODING_AGENT_DIR', ['.pi', 'agent'], process.cwd()), 'sessions', basename(dirname(sourcePath)));
    await mkdir(dir, { recursive: true });
    const name = basename(sourcePath).replace(`_${header.id}.jsonl`, `_${id}.jsonl`);
    await writeFile(join(dir, name), JSON.stringify({ ...header, id }) + (end < 0 ? '\n' : text.slice(end)), { flag: 'wx', mode: 0o600 });
    return id;
  }

  skillDirs(): SkillDir[] {
    // pi keeps skills in its agent dir and also reads the open-standard `~/.agents/skills`, which
    // other agents may share. A synced skill goes to pi's own dir, so it never trips over another
    // agent reading the same folder.
    return [
      { path: join(configDirectory(this.env, 'PI_CODING_AGENT_DIR', ['.pi', 'agent'], process.cwd()), 'skills'), scope: 'personal' },
      { path: join(this.env.HOME ?? process.env.HOME ?? homedir(), '.agents', 'skills'), scope: 'shared' },
    ];
  }

  /** pi's MCP servers: `mcp.json` in its agent dir, the same `{"mcpServers": {…}}` shape Claude
   * uses. Writes touch only the one name, so pi's own fields such as `exposure` survive. */
  private mcpJsonPath(): string {
    return join(configDirectory(this.env, 'PI_CODING_AGENT_DIR', ['.pi', 'agent'], process.cwd()), 'mcp.json');
  }

  /** The prompt pi assembled for this conversation: the system message in its session file. */
  async readSystemPrompt(agentSessionId: string, _cwd: string): Promise<SystemPrompt | null> {
    const root = join(this.env.PI_CODING_AGENT_DIR || process.env.PI_CODING_AGENT_DIR || join(homedir(), '.pi', 'agent'), 'sessions');
    const path = await findSessionFile(root, agentSessionId, 1, (name, id) => name.endsWith(`_${id}.jsonl`));
    if (!path) return null;
    let text: string | null = null;
    await scanJsonl(path, (line) => {
      const found = piSystemPromptLine(line);
      if (found !== null) text = found;
    });
    return text ? { text, source: 'pi 会话文件' } : null;
  }

  mcpServers(): McpServerInfo[] {
    return jsonMcpServers(this.mcpJsonPath());
  }

  mcpInstall(name: string, config: Record<string, unknown>): void {
    installJsonMcpServer(this.mcpJsonPath(), name, config);
  }

  mcpRemove(name: string): void {
    if (!removeJsonMcpServer(this.mcpJsonPath(), name)) throw new Error(`pi 没有名为 ${name} 的 MCP 服务`);
  }

  /**
   * pi has no command that lists sessions, so they are found the way pi itself stores them:
   * one JSONL file per session under `<agent dir>/sessions/<directory>/`. Files are only read.
   */
  async listSessions(): Promise<AgentSessionSummary[]> {
    const root = join(this.env.PI_CODING_AGENT_DIR || process.env.PI_CODING_AGENT_DIR || join(homedir(), '.pi', 'agent'), 'sessions');
    if (!existsSync(root)) return [];
    const out: AgentSessionSummary[] = [];
    for (const dir of readdirSync(root, { withFileTypes: true })) {
      if (!dir.isDirectory()) continue;
      for (const file of readdirSync(join(root, dir.name))) {
        if (!file.endsWith('.jsonl')) continue;
        const path = join(root, dir.name, file);
        try {
          const buf = Buffer.alloc(SESSION_HEAD_BYTES);
          const fd = openSync(path, 'r');
          let read: number;
          try {
            read = readSync(fd, buf, 0, SESSION_HEAD_BYTES, 0);
          } finally {
            closeSync(fd);
          }
          const summary = summarisePiSession(buf.subarray(0, read).toString('utf8'), statSync(path).mtime.toISOString());
          if (summary) out.push(summary);
        } catch {
          // An unreadable or half-written file is skipped rather than failing the whole list.
        }
      }
    }
    return out.sort((a, b) => (b.updatedAt ?? '').localeCompare(a.updatedAt ?? ''));
  }

  /** Opens the session in a short-lived pi and asks for its messages. Opening does not write to it. */
  async loadHistory(agentSessionId: string, cwd: string): Promise<HistoryTurn[]> {
    const session = await PiSession.open(this.command, { cwd, resumeId: agentSessionId }, 'pi', this.env);
    try {
      const data = await session.request('get_messages', {});
      const turns = turnsFromPiMessages(data?.messages ?? []);
      // Where a copy may stop. Older pi builds have no such command; then only whole copies work.
      const forks = await session.request('get_fork_messages', {}).catch(() => null);
      return withPiForkRefs(turns, forks?.messages ?? []);
    } finally {
      await session.close();
    }
  }

  /**
   * `clone` copies the conversation into a new session file and switches this pi to it. `fork`
   * does the same up to, and not including, one of its user messages.
   *
   * pi keeps sessions by directory and will not open one from another directory: it asks whether
   * to fork it there instead. Started with `--fork`, it does that without asking, so a copy for
   * `intoCwd` is made by a pi started there. The copy has every entry of the original under the
   * same id, so cutting it at a turn works as it does at home; that cut is a second copy, and the
   * whole one made first stays behind in pi's list for that directory.
   */
  async forkSession(agentSessionId: string, cwd: string, forkRef?: string, intoCwd?: string): Promise<string> {
    const session = intoCwd
      ? await PiSession.open(this.command, { cwd: intoCwd }, 'pi', this.env, ['--fork', agentSessionId])
      : await PiSession.open(this.command, { cwd, resumeId: agentSessionId }, 'pi', this.env);
    try {
      if (forkRef || !intoCwd) {
        const res = forkRef ? await session.request('fork', { entryId: forkRef }) : await session.request('clone', {});
        if (res?.cancelled) throw new Error('an extension cancelled the fork');
      }
      const state = await session.request('get_state', {});
      const id = String(state?.sessionId ?? '');
      if (!id || id === agentSessionId) throw new Error('pi did not create a new session for the fork');
      return id;
    } finally {
      await session.close();
    }
  }

  /**
   * A copy that stops before one user message, for editing it. pi's own `fork` is exactly that, so
   * there is nothing more to it than naming the message the turn starts with.
   */
  forkBeforeSession(agentSessionId: string, cwd: string, forkBeforeRef: string): Promise<string> {
    return this.forkSession(agentSessionId, cwd, forkBeforeRef);
  }

  async start(opts: StartOptions): Promise<AgentSession> {
    return PiSession.open(this.command, opts, 'pi', this.env);
  }
}
