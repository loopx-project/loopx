import { cliDiagnostic } from './diagnostics.js';
import { execFile } from 'node:child_process';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { readdirSync } from 'node:fs';
import { promisify } from 'node:util';
import { AcpAdapter, airForkPoint } from './acp.js';
import { CodexAppServerAdapter } from './codex.js';
import { TRAE_ICON } from './icons.js';
import { configDirectory, findSessionFile, quoteArg } from './session-location.js';
import type { AgentSession, HistoryTurn, ModelOption, PermissionMode, PlanQuota, QuotaWindow, SlashCommand, StartOptions } from './types.js';

const execFileAsync = promisify(execFile);

/**
 * TRAE CLI uses the Codex-compatible app-server for live turns and native goals.
 * ACP remains the metadata transport; live turns, history and fork use app-server so one protocol owns
 * the native thread while it is active.
 * What is TRAE-specific:
 *
 * - it has no mode that asks before every action. "Workspace Edit" (its default) already approves
 *   edits in the workspace and asks only for what leaves it, which is what the hub calls
 *   auto_edit; "Full Access" approves everything. So the hub offers those two levels only;
 * - the effort config option is called `reasoning_effort` and offers low..xhigh. The shared code
 *   finds it by its category, so nothing here has to know the id;
 * - its ACP server cannot delete a session, but its CLI can (`traex delete --force`);
 * - app-server `thread/fork` can copy through or before a turn, and a copy asked for from another
 *   directory is filed and loads there;
 * - it keeps a per-session folder of files it generated, next to its session log.
 */

const TRAEX_MODE_IDS: Record<PermissionMode, readonly string[]> = {
  ask: ['default'], // never offered to the UI; a safe answer if an old thread still says "ask"
  auto_edit: ['default'],
  full_access: ['bypass_permissions'],
};

/** Effort levels TRAE CLI accepts through the `reasoning_effort` config option. */
const TRAEX_EFFORTS = ['low', 'medium', 'high', 'xhigh'] as const;

interface TraexModelStatus {
  name: string;
  configName?: string;
  loadPercent?: number;
  quota?: QuotaWindow;
}

/** Read only documented model metadata; load is not a quota or a queue position. */
export function parseTraexModelStatus(stdout: string): TraexModelStatus[] {
  const input: unknown = JSON.parse(stdout);
  if (!Array.isArray(input)) throw new Error('TRAE 模型状态格式不正确');
  const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
  return input.flatMap((row) => {
    if (!row || typeof row.name !== 'string' || !row.name) return [];
    const meta = row._meta?.trae;
    const load = meta?.load?.percent;
    const weekly = meta?.weeklyQuota;
    let quota: QuotaWindow | undefined;
    if (weekly?.applies === true) {
      const used = finite(weekly.usedPercent) ? weekly.usedPercent
        : finite(weekly.remainingPercent) ? 100 - weekly.remainingPercent
        : weekly.isDepleted === true ? 100 : undefined;
      if (used !== undefined && used >= 0 && used <= 100) {
        const reset = finite(weekly.resetTime) && weekly.resetTime > 0 ? new Date(weekly.resetTime * 1000) : null;
        quota = { label: '7D', scope: row.name, usedPercent: used,
          ...(reset && Number.isFinite(reset.getTime()) ? { resetsAt: reset.toISOString() } : {}) };
      }
    }
    return [{ name: row.name, ...(typeof row.config_name === 'string' ? { configName: row.config_name } : {}),
      ...(finite(load) && load >= 0 ? { loadPercent: load } : {}), ...(quota ? { quota } : {}) }];
  });
}

/** Shared read-only metadata query for TRAE and its local Ferry gateway. */
export async function readTraexModelStatus(cli: string[], env: NodeJS.ProcessEnv): Promise<TraexModelStatus[]> {
  const { stdout } = await execFileAsync(cli[0], [...cli.slice(1), 'models', '--json'], {
    env: { ...process.env, ...env }, timeout: 15_000, maxBuffer: 2 * 1024 * 1024,
  });
  return parseTraexModelStatus(stdout);
}

export const TRAEX_METADATA = {
  adapter: 'traex' as const,
  label: 'TRAE CLI',
  icon: TRAE_ICON,
};

export class TraexAcpAdapter extends AcpAdapter {
  readonly id = 'traex';
  readonly label = TRAEX_METADATA.label;
  readonly icon = TRAEX_METADATA.icon;
  readonly capabilities = {
    workModes: ['execute', 'plan'] as const,
    speed: true,
    goal: { controls: ['show', 'pause', 'clear'] as const, actions: ['set', 'show', 'pause', 'resume', 'clear'] as const, live: true },
    compact: true,
    coordination: true,
    resume: true,
    modelSwitch: true,
    approvals: true,
    permissionModes: ['auto_edit', 'full_access'] as const,
    effortLevels: TRAEX_EFFORTS,
    images: true,
    deleteSession: true,
    history: true,
    fork: true,
    forkAt: true,
    editMessage: true,
    multiAccount: false,
    forkInto: true,
    quota: true,
  };

  protected readonly modeIds = TRAEX_MODE_IDS;

  protected forkPointMeta(forkRef: string): Record<string, unknown> {
    return airForkPoint(forkRef);
  }

  /** Keep history refs in the same turn-id space as the app-server fork RPC. */
  override loadHistory(agentSessionId: string, _cwd: string): Promise<HistoryTurn[]> {
    return this.execution.loadHistory(agentSessionId);
  }

  /** Fork through a finished turn with app-server; ACP session/fork conflicts with active writers. */
  override forkSession(agentSessionId: string, cwd: string, forkRef?: string, intoCwd?: string): Promise<string> {
    return this.execution.forkSession(agentSessionId, cwd, forkRef, intoCwd);
  }

  /**
   * Editing goes through the app-server, which can fork before a turn; ACP's fork point only names
   * a message to keep, so it cannot express a first turn's empty prefix.
   */
  forkBeforeSession(agentSessionId: string, cwd: string, forkBeforeRef: string): Promise<string> {
    return this.execution.forkBeforeSession(agentSessionId, cwd, forkBeforeRef);
  }

  /** Plain CLI used for app-server, model metadata and session deletion. */
  private readonly cli: string[];
  private readonly execution: CodexAppServerAdapter;

  constructor(
    command: string[],
    env: Record<string, string> = {},
    /** Defaults to the ACP command without the `acp serve` part. */
    cli?: string[],
  ) {
    super(command, env);
    const endsWithServe = command.length > 2 && command.at(-2) === 'acp' && command.at(-1) === 'serve';
    this.cli = cli ?? (endsWithServe ? command.slice(0, -2) : command);
    // TRAE's ImageX references can produce 4016 Invalid image even after a successful upload.
    // Inline image bytes remain readable and do not leave an invalid CDN reference in history.
    // The copy-in staging folder of importSession follows CODEX_HOME; point it at TRAE's own home.
    this.execution = new CodexAppServerAdapter([...this.cli, 'app-server'],
      env.CALLI_TRAEX_HOME ? { ...env, CODEX_HOME: env.CALLI_TRAEX_HOME } : env, 'TRAE CLI', true);
  }

  override diagnose() { return cliDiagnostic(this.cli, this.env, 'app-server / ACP 1'); }

  /** Device SSO is the login flow that does not need a browser on this host. */
  loginCommand(): string {
    const home = this.env.HOME;
    const prefix = home ? `env HOME=${quoteArg(home)} ` : '';
    return `${prefix}${quoteArg(this.cli[0])} login --sso-device`;
  }

  override start(opts: StartOptions): Promise<AgentSession> {
    return this.execution.start(opts);
  }

  override commands(cwd?: string): Promise<SlashCommand[]> {
    return this.execution.commands(cwd);
  }

  private statusRead: Promise<TraexModelStatus[]> | null = null;

  /** Coalesce concurrent requests only; quota caching belongs to ThreadManager. */
  private readModelStatus(): Promise<TraexModelStatus[]> {
    if (this.statusRead) return this.statusRead;
    const read = readTraexModelStatus(this.cli, this.env);
    this.statusRead = read;
    void read.finally(() => { if (this.statusRead === read) this.statusRead = null; }).catch(() => undefined);
    return read;
  }

  override async models(refresh = false): Promise<ModelOption[]> {
    const [models, status, executionModels] = await Promise.all([super.models(refresh), this.readModelStatus().catch(() => []), this.execution.models(refresh).catch(() => [])]);
    return models.map((model) => {
      const execution = model.id === 'default' ? executionModels.find(m => m.isDefault) : executionModels.find(m => m.id === model.id);
      if (execution?.speeds?.length) model = { ...model, speeds: execution.speeds };
      // The ACP default is an alias. Match its concrete app-server model or descriptive target,
      // while keeping the alias description intact so the UI can still name that target.
      const live = status.find((s) => s.configName === model.id || s.name === model.id || s.name === model.label
        || (model.id === 'default' && (s.configName === execution?.id || s.name === execution?.id || s.name === execution?.label || s.name === model.description)));
      if (!live) return model;
      const load = live.loadPercent !== undefined ? { loadPercent: live.loadPercent } : {};
      if (model.id === 'default') return { ...model, ...load };
      const hints: string[] = [];
      if (live.loadPercent !== undefined) hints.push(`模型负载 ${live.loadPercent}%`);
      if (live.quota) hints.push(`每周额度剩余 ${100 - live.quota.usedPercent}%`);
      return { ...model, ...load, description: [...hints, model.description].filter(Boolean).join(' · ') || undefined };
    });
  }

  // Literal usage-limit messages shipped by TRAE CLI's visible error formatter.
  planExhausted(message: string): boolean {
    return /You've hit your usage limit(?:\.| for )|Usage limit reached\. You've reached your usage limit\.|Your workspace is out of credits\./i.test(message);
  }

  async fetchQuota(): Promise<PlanQuota> {
    const status = await this.readModelStatus();
    const models = await super.models().catch(() => []);
    const windows = status.flatMap((s) => s.quota ? [{ ...s.quota, modelIds: models.filter(m => m.id === s.configName || m.id === s.name || m.label === s.name).map(m => m.id), ...(s.loadPercent !== undefined ? { loadPercent: s.loadPercent } : {}) }] : []);
    return { windows, checkedAt: new Date().toISOString(),
      note: windows.length ? '按模型显示每周额度和当前负载；负载越高响应可能越慢，不代表排队人数或等待时间。'
        : 'TRAE 当前未报告每周额度；模型负载请在模型列表查看。' };
  }

  resumeCommand(agentSessionId: string): string {
    return `traex resume ${agentSessionId}`;
  }

  async sessionLocation(agentSessionId: string | null, cwd: string) {
    const configDir = configDirectory(this.env, 'CALLI_TRAEX_HOME', ['.trae', 'cli'], cwd);
    const sessionPath = await findSessionFile(join(configDir, 'sessions'), agentSessionId, 3, (name, id) => name.startsWith('rollout-') && name.endsWith(`-${id}.jsonl`));
    return { configDir, sessionPath };
  }

  /** Another account's session, forked in through the app-server as Codex does it. */
  importSession(sourcePath: string): Promise<string> {
    return this.execution.importSession(sourcePath);
  }

  /** The ACP server has no session/delete; the CLI does it in one short run. */
  async deleteSession(agentSessionId: string, _cwd: string): Promise<void> {
    await execFileAsync(this.cli[0], [...this.cli.slice(1), 'delete', '--force', agentSessionId], {
      env: { ...process.env, ...this.env },
    });
  }

  /**
   * Files TRAE CLI generated for a session live in a folder of its own next to the session log:
   * ~/.trae/cli/sessions/<year>/<month>/<day>/rollout-<timestamp>-<session id>.artifacts
   */
  artifactDirs(agentSessionId: string): string[] {
    if (!/^[0-9a-f-]{8,64}$/i.test(agentSessionId)) return [];
    const home = this.env.CALLI_TRAEX_HOME || process.env.CALLI_TRAEX_HOME || join(homedir(), '.trae', 'cli');
    const sessions = join(home, 'sessions');
    try {
      const years = readdirSync(sessions).filter((d) => /^\d{4}$/.test(d));
      return years.flatMap((year) => {
        const months = readdirSync(join(sessions, year)).filter((d) => /^\d{1,2}$/.test(d));
        return months.flatMap((month) =>
          readdirSync(join(sessions, year, month))
            .filter((d) => /^\d{1,2}$/.test(d))
            .flatMap((day) =>
              readdirSync(join(sessions, year, month, day))
                .filter((d) => d.endsWith(`-${agentSessionId}.artifacts`))
                .map((d) => join(sessions, year, month, day, d)),
            ),
        );
      });
    } catch {
      return []; // no sessions yet, or the folder layout changed
    }
  }
}
