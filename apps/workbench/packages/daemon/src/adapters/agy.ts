import type { WorkMode } from './types.js';
import { cliDiagnostic } from './diagnostics.js';
import { PlanReply } from './plan-reply.js';
import { sessionEnvironment } from './types.js';
import { execFile, spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { homedir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { access, copyFile, cp, mkdir, readdir } from 'node:fs/promises';
import type { EmittedEvent } from '../events.js';
import { imagePathNote } from '../attachments.js';
import { commandExists } from '../util.js';
import { ANTIGRAVITY_ICON } from './icons.js';
import { configDirectory, findSessionFile } from './session-location.js';
import { agyLiveUsage } from './session-usage.js';
import { agyImageTool } from './agy-image.js';
import { agyBrowserTool, agySearchTool } from './agy-web.js';
import type {
  AgentAdapter,
  AgentSession,
  ModelOption,
  PermissionMode,
  PlanQuota,
  QuotaWindow,
  SkillDir,
  SlashCommand,
  StartOptions,
  TurnHooks,
  TurnInput,
  TurnResult,
} from './types.js';

/**
 * Antigravity CLI ("agy"), driven through its headless print mode with one JSON object per line
 * in both directions:
 *
 *   agy --input-format stream-json --output-format stream-json [flags] -p=
 *
 * The process announces its conversation with an `init` event as soon as it starts, then runs one
 * turn per input line: `step_update` events while it works and a `result` event at the end.
 *
 * What headless mode cannot do shapes this adapter:
 * - It cannot ask for permission. Anything that needs approval is refused, so there are two levels
 *   only: edits approved automatically, or everything approved.
 * - Permission level and model are command-line flags, so changing one restarts the process on the
 *   same conversation.
 * - Input is text only, so pictures are passed as file paths; agy looks at them with its own file
 *   viewer. The stream carries neither thinking text nor file contents.
 */

/** How long the command list is reused. Reading it takes two short agy runs. */
const COMMANDS_TTL_MS = 10 * 60_000;
/**
 * Commands agy lists but that make no sense here: the model and effort are flags of the session's
 * process (the hub's own pickers set them), so a one-off run could not change them.
 */
const AGY_SKIPPED_COMMANDS = new Set(['model', 'effort']);

/** A command agy answers itself, as `agy -p /help` lists it: "/usage (quota)<TAB>View model quota usage". */
export interface AgyCliCommand {
  name: string;
  aliases: string[];
  description: string;
}

/** Reads `agy -p /help`. Lines that do not look like a command are ignored. */
export function parseAgyHelp(stdout: string): AgyCliCommand[] {
  const out: AgyCliCommand[] = [];
  for (const line of stdout.split('\n')) {
    const m = /^\/([\w:-]+)(?:\s+\(([^)]*)\))?\s*\t\s*(.*)$/.exec(line.trim());
    if (!m) continue;
    const aliases = (m[2] ?? '').split(/[,\s]+/).filter(Boolean);
    out.push({ name: m[1], aliases, description: m[3].trim() });
  }
  return out;
}

/**
 * Reads `agy -p /usage`, one row per line, tab-separated:
 *   Gemini Models\tWeekly Limit Remaining\t98%\t2026-10-16T11:59:18Z
 * agy reports REMAINING; the panel shows used, so the number flips.
 */
export function parseAgyUsage(stdout: string): QuotaWindow[] {
  const out: QuotaWindow[] = [];
  for (const line of stdout.split('\n')) {
    const cols = line.trim().split('\t');
    if (cols.length < 3) continue;
    const kind = cols[1] ?? '';
    const label = /five hour/i.test(kind) ? '5H' : /weekly/i.test(kind) ? '7D' : null;
    const remaining = Number.parseInt(cols[2] ?? '', 10);
    if (!label || !Number.isFinite(remaining)) continue;
    out.push({
      label,
      scope: cols[0]?.trim() || undefined,
      usedPercent: Math.min(100, Math.max(0, 100 - remaining)),
      ...(cols[3] && !Number.isNaN(Date.parse(cols[3])) ? { resetsAt: cols[3].trim() } : {}),
    });
  }
  return out;
}

/** Reads `agy -p /skills`: one "name<TAB>description" per line. */
export function parseAgySkills(stdout: string): SlashCommand[] {
  const out: SlashCommand[] = [];
  for (const line of stdout.split('\n')) {
    const tab = line.indexOf('\t');
    if (tab <= 0) continue;
    const name = line.slice(0, tab).trim();
    if (/^[\w:.-]+$/.test(name)) out.push({ name, description: line.slice(tab + 1).trim(), kind: 'skill' });
  }
  return out;
}

const cell = (value: string) => value.replace(/\|/g, '\\|').trim() || ' ';

/**
 * What agy printed for a command, ready to show as a message. Its listings are tab-separated
 * columns; those become a table. Anything else (the changelog is Markdown) is passed through.
 */
export function formatAgyOutput(stdout: string): string {
  const lines = stdout.replace(/\s+$/, '').split('\n');
  if (lines.length === 0 || lines.every((l) => !l.trim())) return '（没有输出）';
  if (!lines.every((l) => l.includes('\t'))) return stdout.trim();
  const rows = lines.map((l) => l.split('\t'));
  const width = Math.max(...rows.map((r) => r.length));
  const pad = (r: string[]) => [...r, ...Array(width - r.length).fill('')];
  // The columns have no names; an empty header keeps the table without inventing any.
  const header = `|${Array(width).fill(' ').join('|')}|`;
  const rule = `|${Array(width).fill('---').join('|')}|`;
  return [header, rule, ...rows.map((r) => `|${pad(r).map(cell).join('|')}|`)].join('\n');
}

/** How long a new process may take to announce its conversation. Startup is normally about two seconds. */
const INIT_TIMEOUT_MS = 30_000;
/** How long a cancelled turn may take to stop before the process is killed outright. */
const CANCEL_GRACE_MS = 5_000;
/** How long the model list is reused. Asking agy for it takes a network round trip. */
const MODELS_TTL_MS = 10 * 60_000;

const AGY_TOOL_KIND: Record<string, string> = {
  run_command: 'execute',
  command_status: 'execute',
  send_command_input: 'execute',
  view_file: 'read',
  list_dir: 'read',
  read_url_content: 'read',
  write_to_file: 'edit',
  replace_file_content: 'edit',
  multi_replace_file_content: 'edit',
  sed_file: 'edit',
  notebook_edit: 'edit',
  grep_search: 'search',
  find_by_name: 'search',
  search_web: 'search',
};

const AGY_VERB: Record<string, string> = { write_to_file: 'Write', view_file: 'Read', list_dir: 'List' };

/** A short line for the step list, such as the command or the file being touched. */
function agyToolTitle(name: string, params: any): string {
  if (name === 'run_command') return String(params?.CommandLine ?? name).split('\n')[0];
  const path = params?.TargetFile ?? params?.AbsolutePath ?? params?.DirectoryPath;
  if (typeof path === 'string' && path) {
    const verb = AGY_VERB[name] ?? (AGY_TOOL_KIND[name] === 'edit' ? 'Edit' : name);
    return `${verb} ${path}`;
  }
  const target = params?.Query ?? params?.Pattern ?? params?.Url;
  return typeof target === 'string' && target ? `${name} ${target}` : name;
}

/**
 * Maps one `step_update` to unified events. Pure, so it can be tested without a process.
 * `started` holds the steps already announced, so a step that finishes without having been seen
 * active still gets its start.
 */
export function mapAgyStep(step: any, started: Set<number>): EmittedEvent[] {
  const type = step?.step_type;
  if (type === 'agent_response') {
    return typeof step.text_delta === 'string' && step.text_delta ? [{ type: 'message.delta', data: { text: step.text_delta } }] : [];
  }
  if (type !== 'tool' && type !== 'subagent') return []; // user_input, system_message

  const index = Number(step.step_index);
  const id = `agy-step-${index}`;
  const out: EmittedEvent[] = [];
  if (!started.has(index)) {
    started.add(index);
    const name = String(step.tool_name ?? step.tool_info?.name ?? 'tool');
    const sub = step.subagent_info?.subagents?.[0];
    out.push({
      type: 'tool.start',
      data: {
        id,
        title: sub ? `${sub.role ?? sub.type_name ?? 'Subagent'}: ${String(sub.initial_prompt ?? '').split('\n')[0]}` : agyToolTitle(name, step.tool_info?.parameters),
        kind: AGY_TOOL_KIND[name] ?? 'other',
        status: 'in_progress',
        // The name the model called, kept for the activity page.
        ...(name ? { tool: name } : {}),
        ...(sub?.type_name ? { subagentType: sub.type_name } : {}),
        input: sub ? { prompt: sub.initial_prompt } : step.tool_info?.parameters,
      },
    });
  }
  if (step.state === 'DONE' || step.state === 'ERROR') {
    const failed = step.state === 'ERROR' || Boolean(step.tool_info?.error);
    out.push({
      type: 'tool.end',
      data: { id, status: failed ? 'failed' : 'completed', output: String(step.tool_info?.error?.message ?? step.tool_info?.output ?? '') },
    });
  }
  return out;
}

/** How a turn ended, from agy's `result` event. `cancelled` says whether the hub asked it to stop. */
export function agyTurnResult(result: any, cancelled: boolean): TurnResult {
  if (result?.status !== 'SUCCESS') {
    if (cancelled || result?.error === 'interrupted') return { stopReason: 'cancelled' };
    return { stopReason: 'error', error: String(result?.error ?? 'agy reported an error') };
  }
  const denied = Array.isArray(result.denied_actions) ? result.denied_actions : [];
  if (denied.length > 0) {
    const refused = [...new Set<string>(denied.map((d: any) => String(d?.display_name ?? d?.action ?? 'an action')))];
    return {
      stopReason: 'error',
      error: `Antigravity refused ${refused.join(', ')}: without a terminal it cannot ask for permission, so the turn stopped there. Switch this thread to full access and send the message again.`,
      errorCode: 'permission_refused',
      refused,
    };
  }
  return { stopReason: 'end_turn' };
}

/** The model list as `agy models` prints it: one `id<TAB>label` per line. Anything else is ignored. */
export function parseAgyModels(stdout: string): ModelOption[] {
  const models: ModelOption[] = [];
  for (const line of stdout.split('\n')) {
    const tab = line.indexOf('\t');
    if (tab <= 0) continue;
    const id = line.slice(0, tab).trim();
    const label = line.slice(tab + 1).trim();
    // Effort is part of the model id here ("…-high", "…-low"). Images go in as paths, for every model.
    if (id && !/\s/.test(id)) models.push({ id, label: label || id, efforts: [] });
  }
  return models;
}

/** Flags for one process. Exported so the mapping from the hub's settings is tested directly. */
export function agyArgs(opts: { conversationId?: string; permissionMode?: PermissionMode; model?: string; workMode?: WorkMode }): string[] {
  return [
    '--input-format',
    'stream-json',
    '--output-format',
    'stream-json',
    ...(opts.workMode === 'plan' ? ['--mode', 'plan'] : opts.permissionMode === 'full_access' ? ['--dangerously-skip-permissions'] : ['--mode', 'accept-edits']),
    ...(opts.model ? ['--model', opts.model] : []),
    ...(opts.conversationId ? ['--conversation', opts.conversationId] : []),
    '-p=', // print mode with no prompt of its own: prompts arrive on stdin
  ];
}

interface Turn {
  hooks: TurnHooks;
  resolve(result: TurnResult): void;
  started: Set<number>;
  cancelled: boolean;
  /** In plan mode, the reply held back until it is known whether it is the plan. */
  plan?: PlanReply;
}

class AgySession implements AgentSession {
  private preparing: Promise<void> = Promise.resolve();
  agentSessionId = '';
  private child: ChildProcessWithoutNullStreams | null = null;
  private buffer = '';
  private stderrTail = '';
  private ready: { resolve(): void; reject(err: Error): void } | null = null;
  private turn: Turn | null = null;
  /** A setting changed: the next turn needs a fresh process. */
  private stale = false;
  private permissionMode: PermissionMode | undefined;

  constructor(
    private readonly command: string[],
    private readonly opts: StartOptions,
    /** Answers a command agy handles itself (such as /usage), or resolves to null for anything else. */
    private readonly answerCommand: (text: string) => Promise<string | null>,
    /** Environment overrides of the agent instance this session belongs to. */
    private readonly env: Record<string, string> = {},
  ) {
    this.agentSessionId = opts.resumeId ?? '';
    this.permissionMode = opts.permissionMode;
  }

  /** Starts a process on this session's conversation and waits until it has announced itself. */
  async launch(): Promise<void> {
    const [bin, ...base] = this.command;
    const args = agyArgs({ conversationId: this.agentSessionId || undefined, permissionMode: this.permissionMode, model: this.opts.model, workMode: this.opts.workMode });
    const child = spawn(bin, [...base, ...args], { cwd: this.opts.cwd, stdio: ['pipe', 'pipe', 'pipe'], env: sessionEnvironment(this.env, this.opts.coordinationEnv) });
    this.child = child;
    this.buffer = '';
    this.stderrTail = '';
    this.stale = false;

    child.stdin.on('error', () => undefined); // a write to a process that just died; its exit is what gets reported
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => this.onData(child, chunk));
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk: string) => {
      this.stderrTail = (this.stderrTail + chunk).slice(-600);
      for (const line of chunk.split('\n')) if (line.trim()) console.error(`[agy] ${line}`);
    });
    child.on('exit', (code, signal) => this.onExit(child, `agy exited (code=${code}, signal=${signal})`));
    child.on('error', (err) => this.onExit(child, `agy could not be started: ${err.message}`));

    let timer: NodeJS.Timeout | undefined;
    try {
      await new Promise<void>((resolve, reject) => {
        this.ready = { resolve, reject };
        timer = setTimeout(() => reject(new Error('agy did not start in time')), INIT_TIMEOUT_MS);
      });
    } catch (err) {
      await this.close();
      throw err;
    } finally {
      clearTimeout(timer);
      this.ready = null;
    }
  }

  private onData(child: ChildProcessWithoutNullStreams, chunk: string): void {
    if (child !== this.child) return; // a process that has been replaced
    this.buffer += chunk;
    let idx: number;
    while ((idx = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, idx).trim();
      this.buffer = this.buffer.slice(idx + 1);
      if (line) this.onRecord(line);
    }
  }

  private onRecord(line: string): void {
    let record: any;
    try {
      record = JSON.parse(line);
    } catch {
      console.error(`[agy] non-JSON line on stdout: ${line.slice(0, 200)}`);
      return;
    }
    if (record.event === 'init') {
      if (record.conversation_id) this.agentSessionId = String(record.conversation_id);
      this.ready?.resolve();
    } else if (record.event === 'step_update' && this.turn) {
      const { hooks, plan } = this.turn;
      for (const ev of mapAgyStep(record.step_update, this.turn.started)) {
        if (plan && ev.type === 'message.delta') plan.add(String(ev.data.text ?? ''));
        else {
          if (plan && ev.type === 'tool.start') plan.aside();
          hooks.emit(ev);
        }
      }
    } else if (record.event === 'result' && this.turn) {
      // After an interrupted or failed turn the process is on its way out; the next turn gets a new one.
      if (record.result?.status !== 'SUCCESS') this.stale = true;
      this.finishTurn(agyTurnResult(record.result, this.turn.cancelled));
    }
  }

  private finishTurn(result: TurnResult): void {
    const turn = this.turn;
    if (!turn) return;
    turn.plan?.finish(result.stopReason === 'end_turn');
    this.turn = null;
    turn.resolve(result);
  }

  private onExit(child: ChildProcessWithoutNullStreams, reason: string): void {
    if (child !== this.child) return;
    this.child = null;
    const detail = this.stderrTail.trim().split('\n').pop();
    const message = detail ? `${reason}: ${detail}` : reason;
    this.ready?.reject(new Error(message));
    if (this.turn) this.finishTurn(this.turn.cancelled ? { stopReason: 'cancelled' } : { stopReason: 'error', error: message });
  }

  async prompt(input: TurnInput, hooks: TurnHooks): Promise<TurnResult> {
    let prepared!: () => void;
    this.preparing = new Promise(resolve => { prepared = resolve; });
    try { return await this.promptInternal(input, hooks, prepared); }
    finally { prepared(); }
  }

  private async promptInternal(input: TurnInput, hooks: TurnHooks, prepared: () => void): Promise<TurnResult> {
    if (this.turn) throw new Error('a turn is already running');
    // Commands agy answers itself never reach the model, and do not work in stream mode at all.
    if (input.text.startsWith('/') && input.images.length === 0) {
      try {
        const answer = await this.answerCommand(input.text);
        if (answer !== null) {
          hooks.emit({ type: 'message.delta', data: { text: answer } });
          return { stopReason: 'end_turn' };
        }
      } catch (err) {
        return { stopReason: 'error', error: err instanceof Error ? err.message : String(err) };
      }
    }
    // The process is gone after a cancel, and out of date after a settings change.
    if (this.child && this.stale) await this.close();
    if (!this.child) await this.launch();
    const child = this.child!;
    const live = agyLiveUsage(this.agentSessionId, this.opts.cwd, this.env, sample => hooks.emit({ type: 'usage.sample', data: { ...sample } }));
    await live.start();
    if (this.child !== child) { await live.stop(); return { stopReason: 'error', error: 'agy 已退出' }; }
    try { return await new Promise<TurnResult>((resolve) => {
      // agy's plan mode has no plan of its own to send: what it answers with is the plan.
      const plan = this.opts.workMode === 'plan' ? new PlanReply((ev) => hooks.emit(ev), 'plan') : undefined;
      this.turn = { hooks, resolve, started: new Set(), cancelled: false, plan };
      child.stdin.write(JSON.stringify({ event: 'user', message: { content: input.text + imagePathNote(input.images) } }) + '\n');
      prepared();
    }); } finally { await live.stop(); }
  }

  /** Takes effect on the next turn: the level is a flag of the process. */
  async setWorkMode(mode: WorkMode): Promise<void> {
    if ((this.opts.workMode ?? 'execute') === mode) return;
    this.opts.workMode = mode;
    this.stale = true;
  }

  async setPermissionMode(mode: PermissionMode): Promise<void> {
    if (mode === this.permissionMode) return;
    this.permissionMode = mode;
    this.stale = true;
  }

  /** agy stops a turn on SIGINT, reports it as interrupted and exits; the conversation stays usable. */
  async cancel(): Promise<void> {
    await this.preparing;
    const child = this.child;
    if (!this.turn || !child) return;
    this.turn.cancelled = true;
    child.kill('SIGINT');
    setTimeout(() => {
      if (this.child === child && this.turn?.cancelled) child.kill('SIGKILL');
    }, CANCEL_GRACE_MS).unref();
  }

  async close(): Promise<void> {
    const child = this.child;
    if (!child) return;
    this.child = null;
    if (this.turn) this.finishTurn({ stopReason: 'cancelled' });
    child.stdin.end();
    child.kill();
  }
}

/**
 * Copies a directory's files from one listing of it: a live conversation rotates its newest chunk
 * away while it is being read, and one entry gone is no reason to lose the rest, let alone to hang
 * a recursive copy on a folder that keeps changing under it.
 */
export async function copyDirectorySnapshot(from: string, to: string): Promise<void> {
  const entries = await readdir(from, { withFileTypes: true });
  await mkdir(to, { recursive: true });
  for (const entry of entries) {
    if (entry.isDirectory()) await copyDirectorySnapshot(join(from, entry.name), join(to, entry.name));
    else await copyFile(join(from, entry.name), join(to, entry.name)).catch((err) => {
      if (err?.code !== 'ENOENT') throw err; // rotated away between the listing and the read
    });
  }
}

export const AGY_METADATA = {
  adapter: 'agy' as const,
  label: 'Antigravity',
  icon: ANTIGRAVITY_ICON,
};

/** Adapter for the Antigravity CLI. `command` is the argv prefix, normally just the executable. */
export class AgyAdapter implements AgentAdapter {
  readonly borrowedTools = [agyImageTool, agySearchTool, agyBrowserTool];
  readonly id = 'agy';
  readonly label = AGY_METADATA.label;
  readonly icon = AGY_METADATA.icon;
  readonly capabilities = {
    workModes: ['execute', 'plan'] as const,
    coordination: true,
    resume: true,
    modelSwitch: true,
    approvals: false,
    permissionModes: ['auto_edit', 'full_access'] as const,
    // It cannot ask, so at the lower level every command is refused and the turn stops; new threads
    // start where it can actually work.
    defaultPermissionMode: 'full_access' as const,
    effortLevels: [] as const,
    images: true,
    deleteSession: false,
    history: false,
    fork: false,
    forkAt: false,
    multiAccount: false,
    forkInto: false,
    quota: true,
  };
  private modelCache: { at: number; models: ModelOption[] } | null = null;
  private readonly commandCache = new Map<string, { at: number; value: Promise<{ cli: AgyCliCommand[]; list: SlashCommand[] }> }>();

  constructor(
    private readonly command: string[] = ['agy'],
    /** Extra environment for this agent instance, such as a second account's home. */
    private readonly env: Record<string, string> = {},
  ) {}

  diagnose() { return cliDiagnostic(this.command, this.env, 'stream-json'); }

  async available(): Promise<boolean> {
    return commandExists(this.command[0]);
  }

  // Literal hard-quota message shipped in the CLI; do not classify HTTP 429 or disk quota errors.
  planExhausted(message: string): boolean { return /You have exhausted your quota on this model\./i.test(message); }

  /** `agy -p /usage` is tab-separated: model group, window, REMAINING percent, reset ISO time. */
  async fetchQuota(): Promise<PlanQuota> {
    const stdout = await this.runPrint(process.cwd(), '/usage');
    const models = await this.models().catch(() => []);
    const windows = parseAgyUsage(stdout).map(window => {
      const family = /^(Gemini|Claude)(?: Models)?$/i.exec(window.scope ?? '')?.[1]?.toLowerCase();
      const families = family ? [family] : /^Claude and GPT models$/i.test(window.scope ?? '') ? ['claude', 'gpt'] : [];
      const modelIds = models.filter(model => families.length ? families.some(f => model.id.toLowerCase().startsWith(f + '-') || model.label.toLowerCase().startsWith(f + ' '))
        : model.id === window.scope || model.label === window.scope).map(model => model.id);
      return { ...window, ...(modelIds.length ? { modelIds } : {}) };
    });
    return { windows, checkedAt: new Date().toISOString() };
  }

  async models(refresh = false): Promise<ModelOption[]> {
    if (refresh) this.modelCache = null;
    if (this.modelCache && Date.now() - this.modelCache.at < MODELS_TTL_MS) return this.modelCache.models;
    const [bin, ...base] = this.command;
    const stdout = await new Promise<string>((resolve, reject) => {
      execFile(bin, [...base, 'models'], { timeout: 30_000, env: { ...process.env, ...this.env } }, (err, out) => (err ? reject(err) : resolve(out)));
    });
    const models = parseAgyModels(stdout);
    if (models.length > 0) this.modelCache = { at: Date.now(), models };
    return models;
  }

  /** One `agy -p <text>` run, for the commands agy answers itself. Resolves to what it printed. */
  private runPrint(cwd: string, text: string): Promise<string> {
    const [bin, ...base] = this.command;
    return new Promise((resolve, reject) => {
      execFile(bin, [...base, '-p', text], { cwd, timeout: 60_000, maxBuffer: 4 * 1024 * 1024, env: { ...process.env, ...this.env } }, (err, stdout, stderr) => {
        if (!err) return resolve(stdout);
        const reason = String(stderr).trim().split('\n').pop() || err.message;
        reject(new Error(`agy ${text.split(' ')[0]} failed: ${reason.replace(/^error:\s*/, '')}`));
      });
    });
  }

  /**
   * agy can list what it answers itself (`/help`) and its skills (`/skills`), each in a one-off run.
   * Skills are passed to the model like any message; the others are answered by `answer()`.
   */
  private loadCommands(cwd: string): Promise<{ cli: AgyCliCommand[]; list: SlashCommand[] }> {
    const hit = this.commandCache.get(cwd);
    if (hit && Date.now() - hit.at < COMMANDS_TTL_MS) return hit.value;
    const value = Promise.all([this.runPrint(cwd, '/help'), this.runPrint(cwd, '/skills').catch(() => '')]).then(([help, skills]) => {
      const cli = parseAgyHelp(help).filter((c) => !AGY_SKIPPED_COMMANDS.has(c.name));
      const list: SlashCommand[] = [
        ...cli.map((c): SlashCommand => ({ name: c.name, description: c.description, kind: 'command' })),
        ...parseAgySkills(skills),
      ];
      return { cli, list };
    });
    this.commandCache.set(cwd, { at: Date.now(), value });
    value.catch(() => this.commandCache.delete(cwd));
    return value;
  }

  async commands(cwd?: string): Promise<SlashCommand[]> {
    try {
      return (await this.loadCommands(cwd ?? process.cwd())).list;
    } catch {
      return [];
    }
  }

  /** Runs a command agy answers itself and returns its output as Markdown; null if `text` is not one. */
  private async answer(cwd: string, text: string): Promise<string | null> {
    const name = /^\/([\w:-]+)/.exec(text.trim())?.[1];
    if (!name) return null;
    // A native goal belongs to the current conversation, never the short-lived query process.
    if (name === 'goal') return null;
    let cli: AgyCliCommand[];
    try {
      cli = (await this.loadCommands(cwd)).cli;
    } catch {
      return null; // without the list nothing is known to be a command; the model gets the text
    }
    if (!cli.some((c) => c.name === name || c.aliases.includes(name))) return null;
    return formatAgyOutput(await this.runPrint(cwd, text.trim()));
  }

  resumeCommand(agentSessionId: string): string {
    return `agy --conversation ${agentSessionId}`;
  }

  async sessionLocation(agentSessionId: string | null, cwd: string) {
    const configDir = configDirectory(this.env, 'CALLI_AGY_HOME', ['.gemini', 'antigravity-cli'], cwd);
    const safeId = agentSessionId && /^[a-z0-9_-]+$/i.test(agentSessionId) ? agentSessionId : null;
    const sessionPath = safeId
      ? await findSessionFile(join(configDir, 'brain', safeId, '.system_generated', 'logs'), safeId, 0, (name) => name === 'transcript.jsonl')
      : null;
    return { configDir, sessionPath };
  }

  /**
   * Another account's conversation, copied into this account's folders under the same id: its
   * database, its brain folder and its annotations. The id is written inside the database, so it
   * cannot be renamed; a conversation this account already has is not overwritten.
   */
  async importSession(sourcePath: string): Promise<string> {
    const brain = dirname(dirname(dirname(sourcePath)));
    const id = basename(brain);
    const from = dirname(dirname(brain));
    const to = configDirectory(this.env, 'CALLI_AGY_HOME', ['.gemini', 'antigravity-cli'], process.cwd());
    const db = join('conversations', `${id}.db`);
    if (await access(join(to, db)).then(() => true, () => false)) throw new Error(`this account already has conversation ${id}`);
    for (const dir of ['conversations', 'brain', 'annotations']) await mkdir(join(to, dir), { recursive: true });
    for (const suffix of ['', '-wal', '-shm']) {
      await copyFile(join(from, db + suffix), join(to, db + suffix)).catch((err) => {
        if (suffix === '' || err?.code !== 'ENOENT') throw err;
      });
    }
    await copyDirectorySnapshot(brain, join(to, 'brain', id));
    await cp(join(from, 'annotations', `${id}.pbtxt`), join(to, 'annotations', `${id}.pbtxt`), { errorOnExist: true, force: false }).catch((err) => {
      if (err?.code !== 'ENOENT') throw err;
    });
    return id;
  }

  skillDirs(): SkillDir[] {
    // agy's own skills live under its CLI home; it also reads the shared `~/.agents/skills`.
    // Synced skills go to its own dir, so what lands there is only agy's business.
    return [
      { path: join(configDirectory(this.env, 'CALLI_AGY_HOME', ['.gemini', 'antigravity-cli'], process.cwd()), 'skills'), scope: 'personal' },
      { path: join(this.env.HOME ?? process.env.HOME ?? homedir(), '.agents', 'skills'), scope: 'shared' },
    ];
  }

  /** agy saves what it generates (images, among other things) in a folder of its own per conversation. */
  artifactDirs(agentSessionId: string): string[] {
    if (!/^[0-9a-f-]{8,64}$/i.test(agentSessionId)) return [];
    const home = this.env.CALLI_AGY_HOME || process.env.CALLI_AGY_HOME || join(homedir(), '.gemini', 'antigravity-cli');
    return [join(home, 'brain', agentSessionId)];
  }

  async start(opts: StartOptions): Promise<AgentSession> {
    const session = new AgySession(this.command, opts, (text) => this.answer(opts.cwd, text), this.env);
    await session.launch();
    if (!session.agentSessionId) {
      await session.close();
      throw new Error('agy did not report a conversation id');
    }
    return session;
  }
}
