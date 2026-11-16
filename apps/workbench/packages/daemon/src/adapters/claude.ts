import { execFile } from 'node:child_process';
import { copyClaudeAccount, startClaudeLogin } from './claude-account.js';
import { fetchGatewayQuota } from './gateway-quota.js';
import { claudeLiveUsage } from './session-usage.js';
import { readTraexModelStatus } from './traex.js';
import { ferryModelKey, readFerryQueueObservations } from './ferry.js';
import type { UsageSample } from '../telemetry.js';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { homedir, tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { AcpAdapter, AcpSession, airForkPoint } from './acp.js';
import { CLAUDE_ICON } from './icons.js';
import { titlePlan } from './plans.js';
import type { JsonRpcPeer } from './jsonrpc.js';
import { installJsonMcpServer, jsonMcpServers, removeJsonMcpServer } from '../mcp-format.js';
import { configDirectory, findSessionFile, quoteArg } from './session-location.js';
import { claudeSystemPromptLine, scanJsonl } from './system-prompt.js';
import type { DiscoveredAccount, McpServerInfo, ModelOption, PermissionMode, PlanQuota, QuotaWindow, SkillDir, SlashCommand, StartOptions, SystemPrompt } from './types.js';

/**
 * Claude Code, through the ACP bridge this repo ships with (`@agentclientprotocol/claude-agent-acp`).
 * The protocol handling lives in acp.ts; what is Claude-specific:
 * - its permission modes: "auto" (Claude decides which actions are risky) is the closest match for
 *   auto_edit, acceptEdits is the fallback when the account or adapter version does not offer it;
 * - it answers /cost and /stats in a session without announcing them;
 * - model catalogs are read without selecting each model; access and effort settings are checked
 *   when a session applies its settings;
 * - opt-in gateway discovery reads the gateway's own catalog and capabilities without inference.
 */

/**
 * Our permission levels mapped to Claude's session modes, in order of preference.
 * "auto" (Claude decides which actions are risky) is the closest match for auto_edit;
 * acceptEdits is the fallback when the account or adapter version does not offer it.
 */
const CLAUDE_MODE_IDS: Record<PermissionMode, readonly string[]> = {
  ask: ['default'],
  auto_edit: ['auto', 'acceptEdits'],
  full_access: ['bypassPermissions'],
};

/** What Claude writes into its session as a user message when the user stops it. */
const CLAUDE_INTERRUPT_NOTE = /^\[Request interrupted by user( for tool use)?\]$/;

/** Effort levels Claude accepts through the `effort` config option. */
const CLAUDE_EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const;

interface GatewayCatalogModel {
  id: string;
  display_name?: string;
  capabilities?: {
    effort?: Record<string, boolean | { supported?: boolean }>;
    image_input?: { supported?: boolean };
  };
}

const CLAUDE_GATEWAY_ROUTING_GROUP = /^auto\//;

/** The gateway's key curates the catalog; the picker only hides routing groups, which duplicate the direct models by name. */
function showClaudeGatewayModel(model: GatewayCatalogModel): boolean {
  return !CLAUDE_GATEWAY_ROUTING_GROUP.test(model.id);
}

/** The same opt-in discovery endpoint Claude Code uses for a configured gateway. */
async function gatewayModels(url: string, headers: Record<string, string>, env: NodeJS.ProcessEnv, defaultModel?: string): Promise<ModelOption[]> {
  const endpoint = new URL(url.replace(/\/$/, '') + (/\/v1\/?$/.test(url) ? '/models' : '/v1/models'));
  const signal = AbortSignal.timeout(10000);
  const models: ModelOption[] = [];
  const cursors = new Set<string>();
  for (;;) {
    const response = await fetch(endpoint, { headers, signal });
    if (!response.ok) throw new Error(`Gateway model discovery failed (HTTP ${response.status})`);
    const page = await response.json() as { data?: GatewayCatalogModel[]; has_more?: boolean; last_id?: string };
    if (!Array.isArray(page.data)) throw new Error('Gateway model discovery returned an invalid catalog');
    for (const model of page.data) {
      if (typeof model.id !== 'string' || !model.id) throw new Error('Gateway model discovery returned an invalid model ID');
      if (!showClaudeGatewayModel(model)) continue;
      const effort = model.capabilities?.effort;
      const image = model.capabilities?.image_input?.supported;
      models.push({
        id: model.id, label: typeof model.display_name === 'string' && model.display_name.trim() ? model.display_name : model.id,
        ...(model.id === defaultModel ? { isDefault: true } : {}),
        ...(effort ? { efforts: CLAUDE_EFFORTS.filter(level => typeof effort[level] === 'object' && effort[level].supported === true) } : {}),
        ...(typeof image === 'boolean' ? { images: image } : {}),
      });
    }
    if (!page.has_more) break;
    if (!page.last_id || cursors.has(page.last_id)) throw new Error('Gateway model discovery returned an invalid pagination cursor');
    cursors.add(page.last_id);
    endpoint.searchParams.set('after_id', page.last_id);
  }
  // Local TRAE metadata must never be applied to a remote or unrelated gateway's models.
  if (['localhost', '127.0.0.1', '[::1]'].includes(endpoint.hostname) && models.some(model => model.label.startsWith('Ferry '))) {
    // Optional reads are independent: a missing CLI must not hide a valid queue observation.
    const [status, queues] = await Promise.all([
      readTraexModelStatus([env.LOOCHAT_TRAEX_BIN || 'traex'], env).catch(() => null),
      readFerryQueueObservations(env),
    ]);
    for (const model of models) {
      if (!model.label.startsWith('Ferry ')) continue;
      // Standard and Max share the upstream config; punctuation differs between their public IDs.
      const key = ferryModelKey(model.id);
      const live = status?.find(row => ferryModelKey(row.configName ?? row.name) === key);
      if (live?.loadPercent !== undefined) model.loadPercent = live.loadPercent;
      if (status === null) model.loadError = true;
      // Ferry reports request positions, not total queue size. Never substitute one for the other.
      model.queueSize = null;
      const queue = queues.get(key);
      model.queuePosition = queue?.position ?? null;
      if (queue) model.queueObservedAt = queue.at;
    }
  }
  return models;
}

/**
 * Commands Claude answers in an ACP session without announcing them (checked by sending each one).
 * Most other terminal-only commands, such as /status and /help, answer "isn't available in this
 * environment"; the hub offers its own status panel instead.
 */
const UNLISTED_COMMANDS: SlashCommand[] = [
  { name: 'goal', description: '设置原生目标并持续执行；不带参数查看，clear 清除', hint: '[目标 | clear]', kind: 'command' },
  { name: 'cost', description: 'Show plan usage limits and what is using them' },
  { name: 'stats', description: 'Show plan usage limits and what is using them' },
  { name: 'context', description: 'Show the context window breakdown the agent computed itself' },
];

/** The agent's list plus the commands it answers but does not list, without duplicates. */
export function withUnlistedCommands(listed: SlashCommand[]): SlashCommand[] {
  if (listed.length === 0) return listed; // no session yet: nothing is known about this agent's commands
  const names = new Set(listed.map((c) => c.name));
  return [...listed, ...UNLISTED_COMMANDS.filter((c) => !names.has(c.name))];
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * "Oct 10, 12:40pm" as the CLI prints it: no year, no timezone beyond the "(UTC)" around it.
 * The year is this one, or next one when that would land the reset in the past.
 */
function claudeResetAt(text: string, now: Date): string | undefined {
  const m = /^(\w{3}) (\d{1,2}), (\d{1,2})(?::(\d{2}))?(am|pm)$/.exec(text.trim());
  if (!m) return undefined;
  const month = MONTHS.indexOf(m[1]);
  const day = Number(m[2]);
  const hour = Number(m[3]) % 12 + (m[5] === 'pm' ? 12 : 0);
  for (const year of [now.getUTCFullYear(), now.getUTCFullYear() + 1]) {
    const ts = Date.UTC(year, month, day, hour, Number(m[4] ?? 0));
    if (ts >= now.getTime() - 30 * 86_400_000) return new Date(ts).toISOString();
  }
  return undefined;
}

/**
 * `claude -p "/usage"` output, such as:
 *   Current session: 95% used · resets Oct 10, 12:40pm (UTC)
 *   Current week (all models): 40% used · resets Oct 11, 7am (UTC)
 * Lines that do not match (the advice below, the cost table) are ignored.
 */
export function parseClaudeUsage(text: string, now = new Date()): QuotaWindow[] {
  const out: QuotaWindow[] = [];
  for (const line of text.split('\n')) {
    const m = /^Current (session|week(?: \(all models\))?): (\d+)% used(?: · resets ([^(]+?)\(UTC\))?$/.exec(line.trim());
    if (!m) continue;
    out.push({
      label: m[1] === 'session' ? '5H' : '7D',
      // Both windows cover the account; "all models" is not a separate model pool.
      usedPercent: Number(m[2]),
      ...(m[3] ? { resetsAt: claudeResetAt(m[3], now) } : {}),
    });
  }
  return out;
}

export const CLAUDE_METADATA = {
  adapter: 'claude' as const,
  label: 'Claude Code',
  icon: CLAUDE_ICON,
  installGuide: 'npm install -g @anthropic-ai/claude-code',
};

export class ClaudeAcpAdapter extends AcpAdapter {
  private gatewayProbe: Promise<ModelOption[]> | null = null;
  private gatewayKey = '';
  private gatewayCommandProbe: Promise<void> | null = null;
  protected override liveUsage(id: string, cwd: string, emit: (sample: UsageSample) => void) {
    return claudeLiveUsage(id, cwd, this.env, emit);
  }
  readonly id = 'claude';
  readonly label = CLAUDE_METADATA.label;
  readonly icon = CLAUDE_METADATA.icon;
  readonly installGuide = CLAUDE_METADATA.installGuide;
  readonly capabilities = {
    workModes: ['execute', 'plan'] as const,
    speed: true,
    goal: { controls: ['clear'] as const, actions: ['set', 'show', 'clear'] as const },
    coordination: true,
    resume: true,
    modelSwitch: true,
    approvals: true,
    permissionModes: ['ask', 'auto_edit', 'full_access'] as const,
    effortLevels: CLAUDE_EFFORTS,
    images: true,
    deleteSession: true,
    history: true,
    quota: true,
    compact: true,
    fork: true,
    forkAt: true,
    editMessage: true,
    multiAccount: true,
    createAccount: true,
    // Claude Code finds a conversation from any worktree of its repository, so the copy loads there.
    forkInto: true,
    systemPrompt: true,
  };

  protected forkPointMeta(forkRef: string): Record<string, unknown> {
    return airForkPoint(forkRef);
  }

  /**
   * Claude records a stop as a user message of its own. Read as one, it would join the message
   * sent next into a turn the hub never had, and every turn after it would no longer line up.
   */
  protected override isAgentNote(text: string): boolean {
    return CLAUDE_INTERRUPT_NOTE.test(text);
  }

  protected readonly modeIds = CLAUDE_MODE_IDS;

  /** The configured gateway, when discovery is opted into; null for the CLI's own catalog. */
  private gateway() {
    const configDir = configDirectory(this.env, 'CLAUDE_CONFIG_DIR', ['.claude'], process.cwd());
    let settings: { env?: Record<string, string>; model?: string } = {};
    try {
      settings = JSON.parse(readFileSync(join(configDir, 'settings.json'), 'utf8'));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    // Claude applies settings.env after the process environment. Respect account isolation too.
    const env = { ...process.env, ...this.env, ...settings.env };
    const url = env.ANTHROPIC_BASE_URL;
    if (env.CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY !== '1' || !url) return null;
    const headers: Record<string, string> = { 'anthropic-version': '2023-06-01' };
    if (env.ANTHROPIC_AUTH_TOKEN) headers.Authorization = `Bearer ${env.ANTHROPIC_AUTH_TOKEN}`;
    else if (env.ANTHROPIC_API_KEY) headers['x-api-key'] = env.ANTHROPIC_API_KEY;
    return { url, headers, env, defaultModel: env.ANTHROPIC_MODEL || settings.model };
  }

  override models(refresh = false): Promise<ModelOption[]> {
    let gateway: ReturnType<ClaudeAcpAdapter['gateway']>;
    try { gateway = this.gateway(); } catch (error) { return Promise.reject(error); }
    if (!gateway) {
      const changed = !!this.gatewayKey;
      this.gatewayKey = '';
      this.gatewayProbe = null;
      return super.models(refresh || changed);
    }
    const { url, headers, env, defaultModel } = gateway;
    const key = JSON.stringify([url, headers, defaultModel]);
    if (refresh || key !== this.gatewayKey) this.gatewayProbe = null;
    this.gatewayKey = key;
    if (!this.gatewayProbe) {
      // The gateway's catalog is authoritative: no model switch, inference or quota checks.
      // A failed discovery remains an error, and the next request retries instead of caching it.
      const probe = gatewayModels(url, headers, env, defaultModel).catch(error => {
        if (this.gatewayProbe === probe) this.gatewayProbe = null;
        throw error;
      });
      this.gatewayProbe = probe;
    }
    return this.gatewayProbe;
  }

  protected initialSessionOptions(opts: StartOptions) {
    if (opts.probe) return {};
    const options = { ...(opts.model ? { model: opts.model } : {}), ...(opts.effort ? { effort: opts.effort } : {}) };
    return Object.keys(options).length
      ? { meta: { claudeCode: { options } }, model: !!opts.model, effort: !!opts.effort }
      : {};
  }

  protected configureSession(session: AcpSession, peer: JsonRpcPeer): void {
    const prompt = session.prompt.bind(session);
    session.prompt = async (input, hooks) => {
      if (/^\/goal(?:\s|$)/.test(input.text.trim())) {
        if (input.images.length) return { stopReason: 'error', error: '/goal 不接受图片。' };
        if (/^\/goal\s+(pause|resume)$/.test(input.text.trim())) return { stopReason: 'error', error: '此原生目标支持 /goal 查看和 /goal clear 清除；停止请使用停止按钮。' };
      }
      return prompt(input, hooks);
    };
    session.controlGoal = async (action) => {
      if (action !== 'clear') throw new Error('此 agent 不支持运行中执行该目标操作');
      await session.waitForSubmission();
      await peer.request('_session/goal', { sessionId: session.agentSessionId, action: 'clear' });
      return '已请求清除原生目标。';
    };
  }

  protected extraCommands(listed: SlashCommand[]): SlashCommand[] {
    return withUnlistedCommands(listed);
  }

  override async commands(cwd?: string): Promise<SlashCommand[]> {
    const listed = await super.commands(cwd);
    if (!this.gatewayKey || listed.length) return listed;
    // Gateway discovery skips ACP startup; still discover the CLI's own slash commands on demand.
    this.gatewayCommandProbe ??= this.open({ cwd: tmpdir() }, true)
      .then(async ({ session }) => { await session.close(); })
      .finally(() => { this.gatewayCommandProbe = null; });
    await this.gatewayCommandProbe;
    return super.commands(cwd);
  }

  resumeCommand(agentSessionId: string): string {
    return `claude --resume ${agentSessionId}`;
  }

  /** Sign in again in the same config directory this instance already uses. */
  loginCommand(): string {
    const configDir = configDirectory(this.env, 'CLAUDE_CONFIG_DIR', ['.claude'], process.cwd());
    return `env CLAUDE_CONFIG_DIR=${quoteArg(configDir)} ${quoteArg(this.env.LOOCHAT_CLAUDE_BIN || claudeCli())} auth login`;
  }

  createAccount(directory: string): Promise<Record<string, string>> {
    return copyClaudeAccount(configDirectory(this.env, 'CLAUDE_CONFIG_DIR', ['.claude'], process.cwd()), this.mcpJsonPath(), directory, this.env);
  }

  startLogin(onSuccess: (account?: string) => Promise<void>) {
    return startClaudeLogin(this.env.LOOCHAT_CLAUDE_BIN || claudeCli(), configDirectory(this.env, 'CLAUDE_CONFIG_DIR', ['.claude'], process.cwd()), this.env, onSuccess);
  }

  /** "You've hit your session limit · resets 10:10am (UTC)", and the weekly and usage variants. */
  planExhausted(message: string): boolean {
    return /hit your (?:\w+ )?limit|usage limit reached/i.test(message);
  }

  async sessionLocation(agentSessionId: string | null, cwd: string) {
    const configDir = configDirectory(this.env, 'CLAUDE_CONFIG_DIR', ['.claude'], cwd);
    // A resumed or forked session can belong to another worktree within this account.
    const sessionPath = await findSessionFile(join(configDir, 'projects'), agentSessionId, 1, (name, id) => name === `${id}.jsonl`);
    return {
      configDir,
      sessionPath,
      ...(agentSessionId ? { resumeCommand: `env CLAUDE_CONFIG_DIR=${quoteArg(configDir)} ${quoteArg(this.env.LOOCHAT_CLAUDE_BIN || claudeCli())} --resume ${quoteArg(agentSessionId)}` } : {}),
    };
  }

  /**
   * Another account's transcript, copied into this account's `projects/` under a new id: Claude
   * Code resumes any transcript in its config directory, whichever account wrote it. Only the
   * `sessionId` field of each line is renamed, so paths into the original's folder (large tool
   * results saved beside it) still resolve.
   */
  async importSession(sourcePath: string): Promise<string> {
    const oldId = basename(sourcePath, '.jsonl');
    const id = randomUUID();
    const dir = join(configDirectory(this.env, 'CLAUDE_CONFIG_DIR', ['.claude'], process.cwd()), 'projects', basename(dirname(sourcePath)));
    const text = await readFile(sourcePath, 'utf8');
    await mkdir(dir, { recursive: true });
    await writeFile(join(dir, `${id}.jsonl`), text.split(`"sessionId":"${oldId}"`).join(`"sessionId":"${id}"`), { flag: 'wx', mode: 0o600 });
    return id;
  }

  /** The system prompt the conversation runs with, from the transcript's own snapshot of it. */
  async readSystemPrompt(agentSessionId: string, cwd: string): Promise<SystemPrompt | null> {
    const { sessionPath } = await this.sessionLocation(agentSessionId, cwd);
    if (!sessionPath) return null;
    let text: string | null = null;
    await scanJsonl(sessionPath, (line) => {
      // The newest snapshot wins: a mid-session change of skills or MCP servers is reflected in it.
      const found = claudeSystemPromptLine(line);
      if (found !== null) text = found;
    });
    return text ? { text, source: 'Claude Code 会话文件' } : null;
  }

  skillDirs(): SkillDir[] {
    // Claude keeps skills in its config dir, `~/.claude/skills` unless CLAUDE_CONFIG_DIR moves it.
    return [{ path: join(configDirectory(this.env, 'CLAUDE_CONFIG_DIR', ['.claude'], process.cwd()), 'skills'), scope: 'personal' }];
  }

  /**
   * User-scope MCP servers, which Claude Code keeps in `.claude.json`: in the config directory
   * once CLAUDE_CONFIG_DIR is set, in the home directory otherwise. The file also holds most of
   * Claude's own state, so writes touch only the one name inside `mcpServers`.
   */
  private mcpJsonPath(): string {
    const configDir = configDirectory(this.env, 'CLAUDE_CONFIG_DIR', ['.claude'], process.cwd());
    const home = this.env.HOME ?? process.env.HOME ?? homedir();
    const explicit = this.env.CLAUDE_CONFIG_DIR ?? process.env.CLAUDE_CONFIG_DIR;
    const candidates = explicit ? [join(configDir, '.claude.json'), join(home, '.claude.json')] : [join(home, '.claude.json')];
    return candidates.find((path) => existsSync(path)) ?? candidates[0];
  }

  mcpServers(): McpServerInfo[] {
    return jsonMcpServers(this.mcpJsonPath());
  }

  mcpInstall(name: string, config: Record<string, unknown>): void {
    installJsonMcpServer(this.mcpJsonPath(), name, config);
  }

  mcpRemove(name: string): void {
    if (!removeJsonMcpServer(this.mcpJsonPath(), name)) throw new Error(`claude 没有名为 ${name} 的 MCP 服务`);
  }

  /**
   * One `claude -p "/usage"` run. A local command that spends no turn, but Claude Code 2.x still
   * writes a one-line session for it (one per refresh, per account), so persistence is switched off.
   */
  async fetchQuota(): Promise<PlanQuota> {
    // A gateway's account has no subscription of its own; the gateway reports the plans and keys
    // behind its models, when it can. Otherwise the CLI is asked as for any account.
    const gateway = this.gateway();
    if (gateway) {
      const catalog = await this.models().then(models => models.map(model => model.id), () => null);
      const quota = await fetchGatewayQuota(gateway.url, gateway.headers, catalog);
      if (quota) return quota;
    }
    const configDir = configDirectory(this.env, 'CLAUDE_CONFIG_DIR', ['.claude'], process.cwd());
    const planning = claudePlan(configDir, this.env);
    const stdout = await new Promise<string>((resolve, reject) => {
      execFile(claudeCli(), ['-p', '/usage', '--no-session-persistence'], { timeout: 30_000, maxBuffer: 1024 * 1024, env: { ...process.env, ...this.env } }, (err, out) => {
        if (!err) return resolve(out);
        const reason = String(err.message).split('\n').pop() ?? '';
        reject(new Error(`claude /usage failed: ${reason.slice(0, 200)}`));
      });
    });
    const plan = await planning;
    return { windows: parseClaudeUsage(stdout), ...(plan ? { plan } : {}), checkedAt: new Date().toISOString() };
  }

  /**
   * Accounts already signed in on this host: `~/.claude` plus siblings such as `~/.claude-2` that a
   * second login filled in (`CLAUDE_CONFIG_DIR=… claude` leaves credentials there). Each directory
   * is asked through `claude auth status`, a local run that writes no session, so accounts whose
   * login expired drop out of the list on their own. Directories the CLI cannot answer for are
   * skipped rather than reported broken: discovery must never fail the whole scan.
   */
  async discoverAccounts(home = process.env.HOME ?? homedir()): Promise<DiscoveredAccount[]> {
    let names: string[];
    try {
      names = readdirSync(home);
    } catch {
      return []; // no home to look at: nothing to offer
    }
    // Same shape the PATH scan uses for CLIs, so `.claude.json` (a file, not a config directory)
    // or other clutter is left alone; only directories can hold a login.
    const candidates = names
      .filter((name) => /^\.claude(?=$|[0-9]|[-_.])/.test(name))
      .map((name) => join(home, name))
      .filter((dir) => {
        try {
          return statSync(dir).isDirectory();
        } catch {
          return false;
        }
      });
    const found = await Promise.all(
      candidates.map(async (dir): Promise<DiscoveredAccount | null> => {
        const status = await claudeAuthStatus(dir, this.env);
        if (!status?.loggedIn || !status.email) return null;
        return { email: status.email, env: { CLAUDE_CONFIG_DIR: dir }, isDefault: dir === join(home, '.claude') };
      }),
    );
    return found.filter((a): a is DiscoveredAccount => a !== null);
  }
}

/** The claude CLI used for local one-shot runs: quota reads and account discovery. */
function claudeCli(): string {
  return process.env.LOOCHAT_CLAUDE_BIN ?? process.env.LOOCHAT_CLAUDE_QUOTA_BIN ?? 'claude';
}

const CLAUDE_PLANS: Record<string, string> = { free: 'Free', pro: 'Pro', max: 'Max', team: 'Team', enterprise: 'Enterprise' };

/**
 * The plan's name from what Claude Code keeps about the login: `subscriptionType` (pro, max…) and,
 * for Max, `rateLimitTier`, the only place that tells 5x from 20x.
 */
export function claudePlanName(subscription: unknown, tier?: unknown): PlanQuota['plan'] {
  if (typeof subscription !== 'string' || !subscription.trim()) return undefined;
  const id = subscription.trim();
  const multiple = id.toLowerCase() === 'max' && typeof tier === 'string' ? /max_(\d+)x/i.exec(tier)?.[1] : undefined;
  const label = multiple ? `Max ${multiple}x` : CLAUDE_PLANS[id.toLowerCase()] ?? titlePlan(id);
  return { id: multiple ? `${id} · ${tier}` : id, label };
}

/**
 * The plan of the account in `configDir`. Its credentials file holds the plan next to the login's
 * tokens: only the two plan fields are taken from it, and nothing else of it is kept or sent.
 * Where there is no such file (macOS keeps the login in the Keychain), `claude auth status` still
 * says the subscription, without Max's multiple.
 */
async function claudePlan(configDir: string, extraEnv: Record<string, string>): Promise<PlanQuota['plan']> {
  try {
    const oauth = JSON.parse(readFileSync(join(configDir, '.credentials.json'), 'utf8'))?.claudeAiOauth;
    const plan = claudePlanName(oauth?.subscriptionType, oauth?.rateLimitTier);
    if (plan) return plan;
  } catch {
    // No file or not one the hub understands: ask the CLI instead.
  }
  return claudePlanName((await claudeAuthStatus(null, extraEnv))?.subscriptionType);
}

/** One `claude auth status` run, against a config directory if given; null when it says nothing usable. */
async function claudeAuthStatus(
  configDir: string | null,
  extraEnv: Record<string, string>,
): Promise<{ loggedIn?: boolean; email?: string; subscriptionType?: string } | null> {
  try {
    const stdout = await new Promise<string>((resolve, reject) => {
      execFile(
        claudeCli(),
        ['auth', 'status'],
        { timeout: 15_000, maxBuffer: 64 * 1024, env: { ...process.env, ...extraEnv, ...(configDir ? { CLAUDE_CONFIG_DIR: configDir } : {}) } },
        (err, out) => (err ? reject(err) : resolve(out)),
      );
    });
    return JSON.parse(stdout) as { loggedIn?: boolean; email?: string; subscriptionType?: string };
  } catch {
    return null; // not signed in there, or output the hub does not understand
  }
}
