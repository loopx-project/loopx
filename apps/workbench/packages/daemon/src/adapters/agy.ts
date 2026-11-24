import type { WorkMode } from './types.js';
import { AcpAdapter, type AcpSession } from './acp.js';
import type { UsageSample } from '../telemetry.js';
import { isCompaction } from './compaction.js';
import { AGY_AUTH_ENV, copyAgyAccount, startAgyLogin, withHostGitEnv } from './agy-account.js';
import { HttpError } from '../errors.js';
import { PlanReply } from './plan-reply.js';
import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { access, copyFile, cp, mkdir, readdir, rm } from 'node:fs/promises';
import { DatabaseSync } from 'node:sqlite';
import { ANTIGRAVITY_ICON } from './icons.js';
import { configDirectory, findSessionFile, quoteArg } from './session-location.js';
import { agyLiveUsage } from './session-usage.js';
import { agyImageTool } from './agy-image.js';
import { agyBrowserTool, agySearchTool } from './agy-web.js';
import type {
  AgentSession,
  EffortLevel,
  ModelOption,
  PermissionMode,
  PlanQuota,
  QuotaWindow,
  SkillDir,
  SlashCommand,
  StartOptions,
  TurnHooks,
} from './types.js';

/** Antigravity uses ACP for conversations; print mode remains for account queries only. Per-request usage and fallback thinking are tailed from the native transcript. */

/** How long the command list is reused. Reading it takes two short agy runs. */
const COMMANDS_TTL_MS = 10 * 60_000;
/** Session commands agy handles inside ACP conversations even when not announced yet. */
const AGY_SESSION_COMMANDS: SlashCommand[] = [
  { name: 'goal', description: '设置原生目标并持续执行直到完成', hint: '<目标>', kind: 'command' },
  { name: 'schedule', description: '定时或周期循环执行指令', hint: '[时间/cron]', kind: 'command' },
];
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

const MODELS_TTL_MS = 10 * 60_000;

/** Reasoning effort levels accepted by `agy --effort`. */
export const AGY_EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;
const AGY_EFFORT_SUFFIX = /^(.+)-(low|medium|high|xhigh|max)$/;
const AGY_EFFORT_LABEL = /\s*\((?:low|medium|high|xhigh|max)\)\s*$/i;

/**
 * The model list as `agy models` prints it: one `id<TAB>label` per line. Variants of one model
 * family (`gemini-4-argon-low`, `-medium`, `-high`) fold into a single family entry with its
 * ordered `efforts`, matching `agy`'s own model/effort picker; single-variant models stay fixed.
 */
export function parseAgyModels(stdout: string): ModelOption[] {
  const rows: Array<{ id: string; label: string; baseId?: string; effort?: EffortLevel }> = [];
  const familyEfforts = new Map<string, Set<EffortLevel>>();
  for (const line of stdout.split('\n')) {
    const tab = line.indexOf('\t');
    if (tab <= 0) continue;
    const id = line.slice(0, tab).trim();
    const label = line.slice(tab + 1).trim();
    if (!id || /\s/.test(id)) continue;
    const m = AGY_EFFORT_SUFFIX.exec(id);
    if (m) {
      const baseId = m[1];
      const effort = m[2] as EffortLevel;
      let set = familyEfforts.get(baseId);
      if (!set) {
        set = new Set();
        familyEfforts.set(baseId, set);
      }
      set.add(effort);
      rows.push({ id, label: label || id, baseId, effort });
    } else {
      rows.push({ id, label: label || id });
    }
  }
  const models: ModelOption[] = [];
  const seen = new Set<string>();
  for (const row of rows) {
    const efforts = row.baseId ? familyEfforts.get(row.baseId) : undefined;
    if (row.baseId && efforts && efforts.size > 1) {
      if (seen.has(row.baseId)) continue;
      seen.add(row.baseId);
      const label = row.label.replace(AGY_EFFORT_LABEL, '').trim() || row.baseId;
      models.push({
        id: row.baseId,
        label,
        efforts: AGY_EFFORTS.filter((e) => efforts.has(e)),
        ...(models.length === 0 ? { isDefault: true } : {}),
      });
    } else {
      if (seen.has(row.id)) continue;
      seen.add(row.id);
      models.push({
        id: row.id,
        label: row.label,
        efforts: [],
        ...(models.length === 0 ? { isDefault: true } : {}),
      });
    }
  }
  return models;
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

const AGY_SESSION_ID = /^[a-z0-9_-]{1,128}$/i;

function replaceSameLengthBytes(buf: Buffer, pairs: Array<[Buffer, Buffer]>): Buffer {
  let out = buf;
  let copied = false;
  for (const [from, to] of pairs) {
    if (!from.length || from.length !== to.length) continue;
    let pos = out.indexOf(from);
    while (pos >= 0) {
      if (!copied) { out = Buffer.from(out); copied = true; }
      to.copy(out, pos);
      pos = out.indexOf(from, pos + to.length);
    }
  }
  return out;
}

/**
 * Clones an Antigravity conversation inside `configDir` under a new conversation id: atomically
 * snapshots its SQLite database with `VACUUM INTO`, rewrites `cascade_id` and `trajectory_id`
 * across tables (in-place byte replacement preserves protobuf wire framing for equal-length UUIDs),
 * and copies its brain directory and annotations. Cleans up partial files on failure.
 */
export async function cloneAgySession(configDir: string, sourceId: string, targetId: string): Promise<void> {
  if (!AGY_SESSION_ID.test(sourceId) || !AGY_SESSION_ID.test(targetId)) throw new Error('invalid Antigravity conversation id');
  const sourceDb = join(configDir, 'conversations', `${sourceId}.db`);
  if (!(await access(sourceDb).then(() => true, () => false))) {
    throw new Error(`Antigravity conversation ${sourceId} was not found`);
  }
  const targetDb = join(configDir, 'conversations', `${targetId}.db`);
  if (await access(targetDb).then(() => true, () => false)) {
    throw new Error(`Antigravity conversation ${targetId} already exists`);
  }
  const targetBrain = join(configDir, 'brain', targetId);
  const targetAnn = join(configDir, 'annotations', `${targetId}.pbtxt`);
  for (const dir of ['conversations', 'brain', 'annotations']) await mkdir(join(configDir, dir), { recursive: true });
  try {
    // Snapshot the source DB cleanly (including WAL state) without mutating the source conversation.
    const src = new DatabaseSync(sourceDb, { readOnly: true });
    try {
      src.exec(`VACUUM INTO '${targetDb.replace(/'/g, "''")}'`);
    } finally {
      src.close();
    }
    const dst = new DatabaseSync(targetDb);
    try {
      const tables = dst.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all() as Array<{ name: string }>;
      const hasMeta = tables.some(t => t.name === 'trajectory_meta');
      const meta = hasMeta ? dst.prepare('SELECT trajectory_id, cascade_id FROM trajectory_meta LIMIT 1').get() as { trajectory_id?: string; cascade_id?: string } | undefined : undefined;
      const oldCascade = typeof meta?.cascade_id === 'string' && meta.cascade_id ? meta.cascade_id : sourceId;
      const oldTrajectory = typeof meta?.trajectory_id === 'string' && meta.trajectory_id ? meta.trajectory_id : null;
      const newTrajectory = oldTrajectory ? randomUUID() : null;
      const textPairs: Array<[string, string]> = [[oldCascade, targetId], ...(oldTrajectory && newTrajectory ? [[oldTrajectory, newTrajectory] as [string, string]] : [])];
      const bytePairs: Array<[Buffer, Buffer]> = textPairs.map(([a, b]) => [Buffer.from(a, 'utf8'), Buffer.from(b, 'utf8')]);
      const rewriteText = (value: string) => textPairs.reduce((acc, [from, to]) => acc.replaceAll(from, to), value);
      dst.exec('BEGIN');
      try {
        for (const { name } of tables) {
          const escaped = `"${name.replace(/"/g, '""')}"`;
          const cols = (dst.prepare(`PRAGMA table_info(${escaped})`).all() as Array<{ name: string }>).map(c => c.name);
          if (!cols.length) continue;
          const selectCols = cols.map(c => `"${c.replace(/"/g, '""')}"`).join(', ');
          const setCols = cols.map(c => `"${c.replace(/"/g, '""')}" = ?`).join(', ');
          const rows = dst.prepare(`SELECT rowid AS __rowid__, ${selectCols} FROM ${escaped}`).all() as Array<Record<string, unknown>>;
          const update = dst.prepare(`UPDATE ${escaped} SET ${setCols} WHERE rowid = ?`);
          for (const row of rows) {
            let changed = false;
            const values = cols.map(col => {
              const v = row[col];
              if (typeof v === 'string') {
                const next = rewriteText(v);
                if (next !== v) changed = true;
                return next;
              }
              if (v instanceof Uint8Array) {
                const buf = Buffer.from(v.buffer, v.byteOffset, v.byteLength);
                const next = replaceSameLengthBytes(buf, bytePairs);
                if (next !== buf) { changed = true; return next; }
              }
              return v;
            });
            if (changed) update.run(...(values as any[]), row.__rowid__ as number);
          }
        }
        dst.exec('COMMIT');
      } catch (err) {
        dst.exec('ROLLBACK');
        throw err;
      }
    } finally {
      dst.close();
    }
    const sourceBrain = join(configDir, 'brain', sourceId);
    if (await access(sourceBrain).then(() => true, () => false)) {
      await copyDirectorySnapshot(sourceBrain, targetBrain);
    }
    await cp(join(configDir, 'annotations', `${sourceId}.pbtxt`), targetAnn, { errorOnExist: true, force: false }).catch((err) => {
      if (err?.code !== 'ENOENT') throw err;
    });
  } catch (err) {
    await Promise.all([
      ...['', '-wal', '-shm'].map(s => rm(targetDb + s, { force: true }).catch(() => undefined)),
      rm(targetBrain, { recursive: true, force: true }).catch(() => undefined),
      rm(targetAnn, { force: true }).catch(() => undefined),
    ]);
    throw err;
  }
}

export const AGY_METADATA = {
  adapter: 'agy' as const,
  label: 'Antigravity',
  icon: ANTIGRAVITY_ICON,
};

/** Adapter for the Antigravity CLI. `command` is the argv prefix, normally just the executable. */
export class AgyAdapter extends AcpAdapter {
  readonly prewarm = true;
  readonly borrowedTools = [agyImageTool, agySearchTool, agyBrowserTool];
  readonly id = 'agy';
  readonly label = AGY_METADATA.label;
  readonly icon = AGY_METADATA.icon;
  readonly capabilities = {
    workModes: ['execute', 'plan'] as const,
    goal: { controls: [] as const, actions: ['set'] as const },
    coordination: true,
    resume: true,
    modelSwitch: true,
    approvals: true,
    compact: true,
    permissionModes: ['ask', 'auto_edit', 'full_access'] as const,
    // Preserve the existing default; ACP permission requests are approved by the client in this mode.
    defaultPermissionMode: 'full_access' as const,
    effortLevels: AGY_EFFORTS,
    images: true,
    deleteSession: false,
    history: false,
    fork: true,
    forkAt: false,
    multiAccount: false,
    createAccount: true,
    forkInto: true,
    quota: true,
  };
  private modelCache: { at: number; models: ModelOption[] } | null = null;
  private modelGeneration = 0;
  onModelsChanged?: (models: ModelOption[]) => void;
  private readonly commandCache = new Map<string, { at: number; value: Promise<{ cli: AgyCliCommand[]; list: SlashCommand[] }> }>();

  constructor(
    private readonly cliCommand: string[] = ['agy'],
    /** Extra environment for this agent instance, such as a second account's home. */
    env: Record<string, string> = {},
  ) {
    super([...cliCommand, 'acp'], env.HOME && env.XDG_CONFIG_HOME ? withHostGitEnv(env) : env);
    const configuredIdle = Number(env.LOOCHAT_ACP_BACKGROUND_IDLE_MS ?? env.CALLI_ACP_BACKGROUND_IDLE_MS);
    this.backgroundIdleMs = Number.isFinite(configuredIdle) && configuredIdle > 0 ? configuredIdle : 1500;
  }

  protected readonly modeIds = { ask: ['default'], auto_edit: ['accept-edits'], full_access: ['auto'] };
  protected override resumeVia = 'session/resume' as const;
  protected override syncThinking = true;

  override async forkSession(agentSessionId: string, cwd: string, forkRef?: string): Promise<string> {
    if (forkRef) throw new Error('this agent can only copy a whole conversation');
    const configDir = configDirectory(this.env, 'LOOCHAT_AGY_HOME', ['.gemini', 'antigravity-cli'], cwd);
    const copyId = randomUUID();
    await cloneAgySession(configDir, agentSessionId, copyId);
    return copyId;
  }

  protected override liveUsage(id: string, cwd: string, emit: (sample: UsageSample) => void, onThinking?: (text: string) => void) {
    // Per-request telemetry and fallback thinking come from the native transcript when ACP does not
    // stream agent_thought_chunk; ACP supplies context occupancy and a per-turn total.
    return agyLiveUsage(id, cwd, this.env, emit, onThinking);
  }

  createAccount(directory: string): Promise<Record<string, string>> {
    return copyAgyAccount(configDirectory(this.env, 'LOOCHAT_AGY_HOME', ['.gemini', 'antigravity-cli'], process.cwd()),
      this.env.HOME ?? process.env.HOME ?? homedir(), directory, this.env);
  }

  startLogin(onSuccess: (account?: string) => Promise<void>) {
    return startAgyLogin(this.cliCommand, this.env, async account => {
      this.modelCache = null;
      this.modelGeneration++;
      this.commandCache.clear();
      this.commandsByCwd.clear();
      this.latestCommands = [];
      await onSuccess(account);
      // Catalog propagation can lag behind OAuth. Do not turn a model lookup failure into a login failure.
      void this.models(true).catch(() => {});
    });
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
    if (refresh) { this.modelCache = null; this.modelGeneration++; }
    if (this.modelCache && Date.now() - this.modelCache.at < MODELS_TTL_MS) return this.modelCache.models;
    const generation = this.modelGeneration;
    const [bin, ...base] = this.cliCommand;
    const stdout = await new Promise<string>((resolve, reject) => {
      execFile(bin, [...base, 'models'], { timeout: 30_000, env: { ...process.env, ...this.env } }, (err, out, stderr) => {
        if (!err) { resolve(out); return; }
        // Native signed-out diagnostic only; network, quota and process errors retain their meaning.
        if (/^\s*(?:Error:\s*)?Please sign in to view available models\.(?: Launch the CLI without arguments to sign in\.)?\s*$/m.test(stderr)) {
          reject(new HttpError(409, 'Antigravity is not signed in', { code: 'agent_login_required' }));
        } else reject(err);
      });
    });
    const models = parseAgyModels(stdout);
    if (generation === this.modelGeneration) {
      if (models.length > 0) this.modelCache = { at: Date.now(), models };
      if (refresh) this.onModelsChanged?.(models);
    }
    return models;
  }

  /** One `agy -p <text>` run, for the commands agy answers itself. Resolves to what it printed. */
  private runPrint(cwd: string, text: string): Promise<string> {
    const [bin, ...base] = this.cliCommand;
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
      const cli = parseAgyHelp(help).filter((c) => ['usage', 'help', 'skills', 'changelog'].includes(c.name));
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
    const native = await super.commands(cwd);
    let queries: SlashCommand[] = [];
    try {
      queries = (await this.loadCommands(cwd ?? process.cwd())).list;
    } catch {
      if (!native.length) return [];
    }
    const merged = [...native, ...queries.filter(c => !native.some(n => n.name === c.name))];
    return [...merged, ...AGY_SESSION_COMMANDS.filter(c => !merged.some(m => m.name === c.name))];
  }

  /** Runs a command agy answers itself and returns its output as Markdown; null if `text` is not one. */
  private async answer(cwd: string, text: string): Promise<string | null> {
    const name = /^\/([\w:-]+)/.exec(text.trim())?.[1];
    if (!name) return null;
    // A native goal belongs to the current conversation, never the short-lived query process.
    if (!['usage', 'quota', 'help', 'skills', 'changelog'].includes(name)) return null;
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
    const prefix = Object.entries(this.env).filter(([key, value]) => ['HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'GH_CONFIG_DIR', 'GIT_CONFIG_GLOBAL'].includes(key) || (AGY_AUTH_ENV.includes(key) && value === ''))
      .map(([key, value]) => `${key}=${quoteArg(value)}`).join(' ');
    return `${prefix ? `env ${prefix} ` : ''}${this.cliCommand.map(quoteArg).join(' ')} --conversation ${quoteArg(agentSessionId)}`;
  }

  async sessionLocation(agentSessionId: string | null, cwd: string) {
    const configDir = configDirectory(this.env, 'LOOCHAT_AGY_HOME', ['.gemini', 'antigravity-cli'], cwd);
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
    const to = configDirectory(this.env, 'LOOCHAT_AGY_HOME', ['.gemini', 'antigravity-cli'], process.cwd());
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
      { path: join(configDirectory(this.env, 'LOOCHAT_AGY_HOME', ['.gemini', 'antigravity-cli'], process.cwd()), 'skills'), scope: 'personal' },
      { path: join(this.env.HOME ?? process.env.HOME ?? homedir(), '.agents', 'skills'), scope: 'shared' },
    ];
  }

  /** agy saves what it generates (images, among other things) in a folder of its own per conversation. */
  artifactDirs(agentSessionId: string): string[] {
    if (!/^[0-9a-f-]{8,64}$/i.test(agentSessionId)) return [];
    const home = configDirectory(this.env, 'LOOCHAT_AGY_HOME', ['.gemini', 'antigravity-cli'], process.cwd());
    return [join(home, 'brain', agentSessionId)];
  }

  override async start(opts: StartOptions): Promise<AgentSession> {
    const session = await super.start({ ...opts, permissionMode: opts.permissionMode ?? this.capabilities.defaultPermissionMode });
    const prompt = session.prompt.bind(session);
    session.prompt = async (input, hooks) => {
      if (!input.images.length && /^\/(?:usage|quota|help|skills|changelog)(?:\s|$)/.test(input.text.trim())) {
        try {
          const answer = await this.answer(opts.cwd, input.text);
          if (answer !== null) { hooks.emit({ type: 'turn.phase', data: { phase: 'prompt_sent' } }); hooks.emit({ type: 'message.delta', data: { text: answer } }); return { stopReason: 'end_turn' }; }
        } catch (err) { return { stopReason: 'error', error: String(err instanceof Error ? err.message : err) }; }
      }
      return prompt(input, hooks);
    };
    return session;
  }

  protected override configureSession(session: AcpSession): void {
    let mode: WorkMode = 'execute';
    let permission: PermissionMode = this.capabilities.defaultPermissionMode;
    const setMode = session.setWorkMode.bind(session);
    session.setWorkMode = async value => { await setMode(value); mode = value; };
    const setPermission = session.setPermissionMode.bind(session);
    session.setPermissionMode = async value => { await setPermission(value); permission = value; };
    const update = session.onUpdate.bind(session);
    session.onUpdate = value => {
      const u = value as { sessionUpdate?: string; currentModeId?: string };
      if (u?.sessionUpdate === 'current_mode_update') mode = u.currentModeId === 'plan' ? 'plan' : 'execute';
      update(value);
    };
    const wrapApproval = (requestApproval: TurnHooks['requestApproval']): TurnHooks['requestApproval'] => req => {
      const allow = permission === 'full_access' && mode !== 'plan'
        ? req.options.find(o => o.kind === 'allow_once')
          ?? req.options.find(o => o.kind === 'allow_always')
          ?? req.options.find(o => /^(?:allow|accept)/i.test(o.optionId))
        : undefined;
      return allow ? Promise.resolve({ optionId: allow.optionId }) : requestApproval(req);
    };
    const setSessionHooks = session.setSessionHooks.bind(session);
    session.setSessionHooks = sessionHooks => setSessionHooks({
      ...sessionHooks,
      turnStarted: (id, meta) => {
        const hooks = sessionHooks.turnStarted(id, meta);
        return { ...hooks, requestApproval: wrapApproval(hooks.requestApproval) };
      },
    });
    const prompt = session.prompt.bind(session);
    session.prompt = async (input, hooks) => {
      const plan = mode === 'plan' && !isCompaction(input.text) ? new PlanReply(hooks.emit, `agy-plan-${Date.now()}`) : null;
      const result = await prompt(input, {
        ...hooks,
        requestApproval: wrapApproval(hooks.requestApproval),
        emit: ev => {
          if (plan && ev.type === 'message.delta') plan.add(String(ev.data.text ?? ''));
          else { if (ev.type === 'tool.start') plan?.aside(); hooks.emit(ev); }
        },
      });
      plan?.finish(result.stopReason === 'end_turn');
      return result;
    };
  }
}
