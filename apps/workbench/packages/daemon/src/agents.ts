import { existsSync, readdirSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { delimiter, dirname, isAbsolute, join, resolve } from 'node:path';
import { AgyAdapter, AGY_METADATA } from './adapters/agy.js';
import { ClaudeAcpAdapter, CLAUDE_METADATA } from './adapters/claude.js';
import { ZcodeAdapter, ZCODE_METADATA } from './adapters/zcode.js';
import { CodexAppServerAdapter, CODEX_METADATA } from './adapters/codex.js';
import { DshAcpAdapter, DSH_METADATA } from './adapters/dsh.js';
import { DoubaoAdapter, DOUBAO_METADATA } from './adapters/doubao.js';
import { PiRpcAdapter, PI_METADATA } from './adapters/pi.js';
import { TraexAcpAdapter, TRAEX_METADATA } from './adapters/traex.js';
import type { AgentAdapter, DiscoveredAccount, LoginSession } from './adapters/types.js';
import { HttpError } from './errors.js';

/**
 * One configured agent instance: an adapter (how to talk to it) plus everything that makes this
 * instance its own, such as a second CLI binary or another account's environment. The adapter
 * implementations stay protocol-level; accounts are instances of them.
 */
export interface AgentInstance {
  /** What threads store; also the key in the adapter map. */
  id: string;
  adapter: 'claude' | 'pi' | 'agy' | 'traex' | 'codex' | 'zcode' | 'dsh' | 'doubao';
  label?: string;
  /** Hidden from the new-thread picker; threads and settings keep working. */
  hidden?: boolean;
  /** Full argv. Defaults to the adapter's own default command. */
  command?: string[];
  /** Merged over the daemon's environment for everything this instance spawns. */
  env?: Record<string, string>;
  /** The login this instance runs as, when it is a linked account (an email, for the picker). */
  account?: string | null;
}

interface InstanceFile {
  agents: AgentInstance[];
}

/**
 * What the person deleted, so a later discovery scan does not bring it straight back: the
 * executables an instance ran (real paths, symlink aliases and all) and the env entries that
 * picked its account. Persona hit this on Claude config directories: a deleted account came
 * back on the next scan. Adding the same thing back by hand still works.
 */
interface RemovedFile {
  paths: string[];
  /** Canonical `KEY=value` entries, one per env variable of a removed instance. */
  envs: string[];
}

const ADAPTER_LABELS: Record<AgentInstance['adapter'], string> = {
  claude: 'Claude Code',
  pi: 'pi',
  agy: 'Antigravity',
  traex: 'TRAE CLI',
  codex: 'Codex',
  zcode: 'ZCode',
  dsh: 'DeepSeek Harness',
  doubao: DOUBAO_METADATA.label,
};

/** How each adapter is built. One line per protocol; instances differ only in command and env. */
function buildAdapter(
  instance: AgentInstance,
  /** Commands of the built-in instances: an instance without its own command inherits its adapter's. */
  defaults: Map<AgentInstance['adapter'], string[]>,
): AgentAdapter {
  const command = instance.command ?? defaults.get(instance.adapter) ?? [];
  const env = instance.env ?? {};
  let adapter: AgentAdapter;
  switch (instance.adapter) {
    case 'claude':
      adapter = new ClaudeAcpAdapter(command, env);
      break;
    case 'pi':
      adapter = new PiRpcAdapter(command, env);
      break;
    case 'agy':
      adapter = new AgyAdapter(command, env);
      break;
    case 'traex':
      adapter = new TraexAcpAdapter(command, env);
      break;
    case 'zcode':
      adapter = new ZcodeAdapter(command, env);
      break;
    case 'codex':
      adapter = new CodexAppServerAdapter(command, env);
      break;
    case 'dsh':
      adapter = new DshAcpAdapter(command, env);
      break;
    case 'doubao':
      adapter = new DoubaoAdapter(command, env);
      break;
  }
  // An instance may carry its own name ("Claude 工作号"); otherwise the adapter's own label stands.
  if (instance.label) (adapter as { label: string }).label = instance.label;
  return adapter;
}

/** The default instances when nothing is configured: one of each built-in agent. */
export function defaultInstances(opts: {
  claudeCommand: string[];
  piCommand: string[];
  agyCommand: string[];
  traexCommand: string[];
  codexCommand: string[];
  zcodeCommand?: string[];
  dshCommand?: string[];
  doubaoCommand?: string[];
}): AgentInstance[] {
  return [
    { id: 'claude', adapter: 'claude', command: opts.claudeCommand },
    { id: 'pi', adapter: 'pi', command: opts.piCommand },
    { id: 'agy', adapter: 'agy', command: opts.agyCommand },
    { id: 'traex', adapter: 'traex', command: opts.traexCommand },
    { id: 'codex', adapter: 'codex', command: opts.codexCommand },
    { id: 'zcode', adapter: 'zcode', command: opts.zcodeCommand ?? ['zcode', 'app-server'] },
    { id: 'dsh', adapter: 'dsh', command: opts.dshCommand ?? ['dsh', '--profile', 'acp'] },
    { id: 'doubao', adapter: 'doubao', command: opts.doubaoCommand ?? ['doubao'] },
  ];
}

export interface SupportedAgent {
  adapter: AgentInstance['adapter'];
  label: string;
  icon?: string;
  installGuide?: string;
  capabilities?: { createAccount: boolean; streamBridge?: AgentAdapter['capabilities']['streamBridge'] };
}

export const SUPPORTED_AGENTS: SupportedAgent[] = [
  CLAUDE_METADATA,
  CODEX_METADATA,
  PI_METADATA,
  AGY_METADATA,
  TRAEX_METADATA,
  ZCODE_METADATA,
  DSH_METADATA,
  DOUBAO_METADATA,
];

/** An executable on PATH that looks like another copy of a known agent CLI. */
export interface DiscoveredAgent {
  /** The proposed instance id: the executable's basename. */
  id: string;
  adapter: AgentInstance['adapter'];
  /** The absolute path found on PATH. */
  command: string;
  label?: string;
  icon?: string;
}

/** A signed-in account of a multi-account agent, shaped so the UI can link it in one click. */
export interface AccountToLink {
  adapter: AgentInstance['adapter'];
  email: string;
  /** Instance env built by the adapter (for Claude, the account's own CLAUDE_CONFIG_DIR). */
  env: Record<string, string>;
  /** Suggested instance id and label: the first free "claude-2", "Claude Code 2". */
  id: string;
  label: string;
}

/**
 * Basenames of CLIs the hub can already talk to. A name is the CLI itself or a numbered copy
 * (`agy2`, `claude-2`); lookalikes such as `ping`, `claude-agent-acp` and `traex-bridge` are left
 * alone. A suffix is a number, optionally after a separator, so an arbitrary word after a dash
 * does not turn another tool into a copy.
 */
const KNOWN_PREFIXES: Array<[AgentInstance['adapter'], RegExp]> = [
  ['claude', copyOf('claude')],
  ['pi', copyOf('pi')],
  ['agy', copyOf('agy')],
  ['codex', copyOf('codex')],
  ['zcode', copyOf('zcode')],
  ['dsh', copyOf('dsh')],
  ['doubao', copyOf('doubao')],
  // traex and the compatibility names its installer creates for the same CLI
  ['traex', copyOf('traex')],
  ['traex', copyOf('traecli')],
  ['traex', copyOf('trae-cli')],
  ['traex', copyOf('trae-agent')],
  ['traex', copyOf('coco')],
  ['traex', copyOf('ta')],
];

/** Aliases that share one executable: the adapter's own name wins when it is installed. */
const PREFERRED_NAMES: Record<AgentInstance['adapter'], string[]> = {
  claude: ['claude'],
  pi: ['pi'],
  agy: ['agy'],
  traex: ['traex', 'traecli', 'trae-cli', 'trae-agent', 'coco', 'ta'],
  codex: ['codex'],
  zcode: ['zcode'],
  dsh: ['dsh'],
  doubao: ['doubao'],
};

/**
 * Owns the agent instances: reads them from `agents.json` (or the built-in defaults), builds one
 * adapter per instance, and can find and add more CLIs later. The adapter map is live: adding an
 * instance makes it usable without a restart.
 */
export class AgentRegistry {
  readonly adapters = new Map<string, AgentAdapter>();
  private readonly instances = new Map<string, AgentInstance>();
  private readonly file: string | null;
  private readonly defaultCommands = new Map<AgentInstance['adapter'], string[]>();
  private readonly logins = new Map<string, { id: string; session: LoginSession }>();
  private readonly removedFile: string | null;
  private removed: RemovedFile = { paths: [], envs: [] };
  private autoJoinTimer: ReturnType<typeof setInterval> | null = null;
  private creating: Promise<unknown> = Promise.resolve();
  private writing: Promise<unknown> = Promise.resolve();

  constructor(
    instances: AgentInstance[],
    /** Where `agents.json` lives, or null for a list the hub does not own (an inline
     *  `CALLI_AGENTS_JSON` hub, or a read-only registry in tests). */
    file: string | null,
    /** The built-in commands, kept as a fallback after the instance that supplied one is removed. */
    builtIn: AgentInstance[] = [],
    private readonly dataDir: string | null = file ? dirname(file) : null,
  ) {
    // The first instance of an adapter decides what an instance without a command runs.
    for (const instance of instances) {
      if (!this.defaultCommands.has(instance.adapter) && instance.command) {
        this.defaultCommands.set(instance.adapter, instance.command);
      }
    }
    // Removing the only instance that had a command must not break the next account of that adapter.
    for (const instance of builtIn) {
      if (!this.defaultCommands.has(instance.adapter) && instance.command) {
        this.defaultCommands.set(instance.adapter, instance.command);
      }
    }
    for (const instance of instances) this.register(instance);
    this.file = file;
    this.removedFile = dataDir ? join(dataDir, 'removed-agents.json') : null;
    if (this.removedFile && existsSync(this.removedFile)) {
      try {
        const parsed = JSON.parse(readFileSync(this.removedFile, 'utf8')) as RemovedFile;
        if (Array.isArray(parsed.paths) && Array.isArray(parsed.envs)) {
          this.removed = {
            paths: parsed.paths.filter((p): p is string => typeof p === 'string'),
            envs: parsed.envs.filter((e): e is string => typeof e === 'string'),
          };
        }
      } catch {
        // A broken file is reported, not fatal: auto-join simply forgets what was removed.
        console.warn('warning: removed-agents.json is not valid JSON; ignoring it');
      }
    }
  }

  /** Builds a registry from the environment: `CALLI_AGENTS_JSON` inline, `agents.json` in the
   *  data dir, or the defaults. An inline list is read-only, and {@link autoJoin} leaves it
   *  alone: it names a test or script machine, not the person's hub. */
  static fromEnv(opts: {
    dataDir: string;
    env: NodeJS.ProcessEnv;
    defaults: AgentInstance[];
  }): AgentRegistry {
    const file = join(opts.dataDir, 'agents.json');
    let instances: AgentInstance[] | null = null;
    const inline = opts.env.CALLI_AGENTS_JSON;
    const source = inline ?? (existsSync(file) ? readFileSync(file, 'utf8') : null);
    if (source) {
      try {
        const parsed = JSON.parse(source) as InstanceFile;
        if (Array.isArray(parsed.agents)) instances = parsed.agents;
      } catch {
        // A broken file is reported, not fatal: the defaults keep the hub usable.
        console.warn('warning: agents.json is not valid JSON; using the built-in agents');
      }
    }
    return new AgentRegistry(instances ?? opts.defaults, inline ? null : file, opts.defaults, opts.dataDir);
  }

  list(): AgentInstance[] {
    return [...this.instances.values()];
  }

  /** Whether this hub owns its agent list as a file. False for an inline `CALLI_AGENTS_JSON`
   *  list — a test or script machine, whose list (and machine) must not be scanned as the
   *  person's own. */
  get ownsFile(): boolean {
    return this.file !== null;
  }

  get(id: string): AgentInstance {
    const instance = this.instances.get(id);
    if (!instance) throw new HttpError(404, `unknown agent: ${id}`);
    return instance;
  }

  /** Updates hub-owned settings. Its id is what threads store, so it never changes. */
  async update(id: string, patch: { label?: unknown; hidden?: unknown; streamBridge?: unknown }): Promise<AgentInstance> {
    return this.mutate(async () => {
      const instance = this.get(id);
      if (patch.label === undefined && patch.hidden === undefined && patch.streamBridge === undefined) throw new HttpError(400, 'a setting is required');
      let next = { ...instance };
      if (patch.label !== undefined) {
        const label = typeof patch.label === 'string' ? patch.label.trim().slice(0, 60) : '';
        if (!label) throw new HttpError(400, 'a label is required');
        next.label = label;
      }
      if (patch.hidden !== undefined) {
        if (typeof patch.hidden !== 'boolean') throw new HttpError(400, 'hidden must be a boolean');
        next.hidden = patch.hidden;
      }
      if (patch.streamBridge !== undefined) next = await this.withStreamBridge(next, patch.streamBridge);
      await this.persist(this.list().map((i) => (i.id === id ? next : i)));
      this.instances.set(id, next);
      const adapter = this.adapters.get(id) as { label: string } | undefined;
      if (patch.streamBridge !== undefined) this.adapters.set(id, buildAdapter(next, this.defaultCommands));
      else if (adapter && patch.label !== undefined) adapter.label = next.label!;
      return next;
    });
  }

  /** Removes only the hub's registration; the agent's own files and login are left alone. The
   *  instance's identity is remembered, so a later auto-join does not add it straight back. */
  async remove(id: string): Promise<AgentInstance> {
    return this.mutate(async () => {
      const instance = this.get(id);
      this.rememberRemoved(instance);
      await this.persistRemoved();
      await this.persist(this.list().filter((i) => i.id !== id));
      this.logins.get(id)?.session.cancel();
      this.logins.delete(id);
      this.instances.delete(id);
      this.adapters.delete(id);
      return instance;
    });
  }

  supported(): SupportedAgent[] {
    return SUPPORTED_AGENTS.map(meta => {
      const adapter = buildAdapter({ id: meta.adapter, adapter: meta.adapter }, this.defaultCommands);
      return { ...meta, capabilities: {
        createAccount: !!adapter.createAccount,
        ...(adapter.capabilities.streamBridge ? { streamBridge: adapter.capabilities.streamBridge } : {}),
      } };
    });
  }

  /** Serialize allocation so simultaneous clicks cannot choose the same instance id. */
  createAccount(input: { adapter?: unknown; sourceId?: unknown; label?: unknown }): Promise<AgentInstance> {
    const task = this.creating.then(() => this.copyAccount(input));
    this.creating = task.catch(() => undefined);
    return task;
  }

  private async copyAccount(input: { adapter?: unknown; sourceId?: unknown; label?: unknown }): Promise<AgentInstance> {
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new HttpError(400, 'an account configuration is required');
    if (input.label !== undefined && typeof input.label !== 'string') throw new HttpError(400, 'label must be a string');
    const meta = SUPPORTED_AGENTS.find(item => item.adapter === input.adapter);
    if (!meta) throw new HttpError(400, 'unknown adapter');
    if (!this.dataDir) throw new HttpError(400, 'account storage is not configured');
    const source = input.sourceId !== undefined ? this.get(String(input.sourceId)) : this.list().find(item => item.adapter === meta.adapter);
    if (source && source.adapter !== meta.adapter) throw new HttpError(400, 'source adapter does not match');
    const adapter = source ? this.adapters.get(source.id)! : buildAdapter({ id: meta.adapter, adapter: meta.adapter }, this.defaultCommands);
    if (!adapter.createAccount) throw new HttpError(400, 'this agent cannot create independent accounts');
    if (!await adapter.available()) throw new HttpError(400, 'install the agent CLI first');
    let n = 1;
    let id: string = meta.adapter;
    while (this.instances.has(id)) id = `${meta.adapter}-${++n}`;
    const root = join(this.dataDir, 'agent-accounts');
    await mkdir(root, { recursive: true, mode: 0o700 });
    const directory = await mkdtemp(join(root, `${id}-`));
    try {
      const env = await adapter.createAccount(directory);
      return await this.add({ id, adapter: meta.adapter, label: input.label || `${meta.label}${n > 1 ? ` ${n}` : ''}`, command: source?.command, env, account: null });
    } catch (error) {
      await rm(directory, { recursive: true, force: true });
      throw error;
    }
  }

  startLogin(agent: string) {
    this.get(agent);
    const adapter = this.adapters.get(agent)!;
    if (!adapter.startLogin) throw new HttpError(400, 'this agent does not support browser login');
    const existing = this.logins.get(agent);
    if (existing && ['starting', 'waiting', 'submitting'].includes(existing.session.state().status)) {
      return { id: existing.id, ...existing.session.state() };
    }
    existing?.session.cancel();
    const id = randomUUID();
    const session = adapter.startLogin(account => this.mutate(async () => {
      if (this.logins.get(agent)?.id !== id) return;
      const next = { ...this.get(agent), account };
      await this.persist(this.list().map(item => item.id === agent ? next : item));
      this.instances.set(agent, next);
    }));
    this.logins.set(agent, { id, session });
    return { id, ...session.state() };
  }

  login(agent: string, id: string): LoginSession {
    this.get(agent);
    const login = this.logins.get(agent);
    if (!login || login.id !== id) throw new HttpError(404, 'login not found; start again');
    return login.session;
  }

  dispose(): void {
    if (this.autoJoinTimer) clearInterval(this.autoJoinTimer);
    this.autoJoinTimer = null;
    for (const login of this.logins.values()) login.session.cancel();
    this.logins.clear();
  }

  /**
   * Accounts of multi-account agents already signed in on this host that no instance uses yet.
   * Each adapter knows how to find its own (Claude: `~/.claude`-style directories); the registry
   * adds what only it knows — which accounts instances already claim, and which ids are free.
   */
  async discoverAccounts(): Promise<AccountToLink[]> {
    const home = process.env.HOME ?? homedir();
    const byAdapter = new Map<AgentInstance['adapter'], AgentInstance[]>();
    for (const instance of this.instances.values()) {
      const list = byAdapter.get(instance.adapter) ?? [];
      list.push(instance);
      byAdapter.set(instance.adapter, list);
    }
    const out: AccountToLink[] = [];
    // Suggested ids must not collide with real instances nor with each other within this scan.
    const takenIds = new Set(this.instances.keys());
    // Every supported adapter, so discovery still works after its last instance is removed.
    for (const { adapter: kind } of SUPPORTED_AGENTS) {
      const instances = byAdapter.get(kind) ?? [];
      // The adapter of an instance that pins no account env of its own; it is the neutral one to ask.
      const neutral = instances.find((i) => !i.env || Object.keys(i.env).length === 0) ?? instances[0];
      const fallback = neutral ? null : this.defaultCommands.get(kind);
      const adapter = neutral
        ? this.adapters.get(neutral.id)
        : fallback
          ? buildAdapter({ id: kind, adapter: kind, command: fallback }, this.defaultCommands)
          : undefined;
      if (!adapter?.discoverAccounts) continue;
      // An account is claimed when an instance sets the same env, or when an env-less instance
      // exists and the account is the adapter's default one.
      const claimed = (account: DiscoveredAccount) =>
        instances.some(
          (i) =>
            Object.entries(i.env ?? {}).some(([k, v]) => account.env[k] === v) ||
            (!i.env || Object.keys(i.env).length === 0) && account.isDefault === true,
        );
      for (const account of await adapter.discoverAccounts(home)) {
        if (claimed(account)) continue;
        let id: string;
        let label: string;
        if (!instances.length && !takenIds.has(kind)) {
          id = kind;
          label = ADAPTER_LABELS[kind];
        } else {
          let n = 2;
          while (takenIds.has(`${kind}-${n}`)) n += 1;
          id = `${kind}-${n}`;
          label = `${ADAPTER_LABELS[kind]} ${n}`;
        }
        takenIds.add(id);
        out.push({ adapter: kind, email: account.email, env: account.env, id, label });
      }
    }
    return out.sort((a, b) => a.email.localeCompare(b.email));
  }

  private register(instance: AgentInstance): void {
    this.instances.set(instance.id, instance);
    this.adapters.set(instance.id, buildAdapter(instance, this.defaultCommands));
  }

  /** Adds an instance and persists it to `agents.json`. */
  async add(input: {
    id?: unknown;
    adapter?: unknown;
    label?: unknown;
    command?: unknown;
    env?: unknown;
    streamBridge?: unknown;
    /** The login when this instance is a linked account (an email). */
    account?: unknown;
  }): Promise<AgentInstance> {
    return this.mutate(async () => {
      const adapter = String(input.adapter ?? '');
      if (!(adapter in ADAPTER_LABELS)) throw new HttpError(400, `unknown adapter: ${adapter}`);
      const id = String(input.id ?? '').trim() || basenameOf(input.command);
      if (!id) throw new HttpError(400, 'an id or a command is required');
      if (!/^[\w.-]{1,40}$/.test(id)) throw new HttpError(400, 'ids are letters, digits, dash, dot and underscore, up to 40');
      if (this.instances.has(id)) throw new HttpError(409, `there already is an agent called ${id}`);
      const command = Array.isArray(input.command) && input.command.length
        ? input.command.map(String)
        : undefined;
      const label = typeof input.label === 'string' && input.label.trim() ? input.label.trim().slice(0, 60) : undefined;
      const account =
        input.account === null ? null : typeof input.account === 'string' && input.account.trim() ? input.account.trim().slice(0, 120) : undefined;
      const env =
        input.env && typeof input.env === 'object' && !Array.isArray(input.env)
          ? Object.fromEntries(Object.entries(input.env as Record<string, unknown>).map(([k, v]) => [String(k), String(v)]))
          : undefined;

      let instance: AgentInstance = {
        id,
        adapter: adapter as AgentInstance['adapter'],
        // Without a name it would inherit the adapter's own ("Antigravity"), which every copy of
        // that CLI would share. The id is what the user typed or what PATH had, so it reads better.
        label: label ?? id,
        ...(command ? { command } : {}),
        ...(env ? { env } : {}),
        ...(account !== undefined ? { account } : {}),
      };
      if (input.streamBridge !== undefined) instance = await this.withStreamBridge(instance, input.streamBridge);
      await this.persist([...this.list(), instance]);
      this.register(instance);
      return instance;
    });
  }

  /** Adapter-owned settings stay in the instance environment, never the agent's native config. */
  private async withStreamBridge(instance: AgentInstance, enabled: unknown): Promise<AgentInstance> {
    if (typeof enabled !== 'boolean') throw new HttpError(400, 'streamBridge must be a boolean');
    const adapter = buildAdapter(instance, this.defaultCommands);
    if (!adapter.capabilities.streamBridge || !adapter.streamBridgeEnvironment) throw new HttpError(400, 'this agent has no experimental stream bridge');
    // Incompatible installs still save the preference; the adapter's turns fall back to text output.
    const { environment } = await adapter.streamBridgeEnvironment(enabled);
    return { ...instance, env: { ...instance.env, ...environment } };
  }

  /** Keep disk snapshots and the live registry in the same order, including login callbacks. */
  private mutate<T>(change: () => Promise<T>): Promise<T> {
    const task = this.writing.then(change);
    this.writing = task.catch(() => undefined);
    return task;
  }

  private async persist(instances: AgentInstance[] = this.list()): Promise<void> {
    const file = this.file;
    if (!file) return;
    const body = JSON.stringify({ agents: instances }, null, 2) + '\n';
    const fs = await import('node:fs/promises');
    const temporary = `${file}.${randomUUID()}.tmp`;
    try {
      await fs.writeFile(temporary, body, { encoding: 'utf8', mode: 0o600 });
      await fs.rename(temporary, file);
    } finally {
      await fs.rm(temporary, { force: true });
    }
  }

  /**
   * Looks along PATH for executables that look like another copy of a known CLI (`agy2`, `claude2`).
   * Only basenames the hub can talk to are offered; the rest of PATH is ignored. Names that resolve
   * to the same executable are listed once, and an executable an instance already runs (including
   * through a path argument such as Claude's ACP bridge) is not offered again.
   */
  async discover(): Promise<DiscoveredAgent[]> {
    const dirs = (process.env.PATH ?? '').split(delimiter).filter(Boolean);
    const taken = new Set(this.instances.keys());
    const takenPaths = new Set<string>();
    for (const instance of this.instances.values()) {
      const command = instance.command ?? this.defaultCommands.get(instance.adapter) ?? [];
      const base = basenameOf(command);
      if (base) taken.add(base);
      for (const [i, token] of command.entries()) {
        // The executable, and any path-like argument, may name the same program as a PATH entry.
        if (i > 0 && !token.includes('/') && !token.includes('\\')) continue;
        const path = resolveCommandPath(token, dirs);
        if (path) takenPaths.add(realPathOf(path));
      }
    }

    const found = new Map<string, DiscoveredAgent>(); // by real path: aliases collapse here
    const seen = new Set<string>();
    for (const dir of dirs) {
      let names: string[];
      try {
        names = readdirSync(dir);
      } catch {
        continue; // unreadable or gone
      }
      for (const name of names) {
        if (taken.has(name) || seen.has(name)) continue;
        const adapter = KNOWN_PREFIXES.find(([, re]) => re.test(name))?.[0];
        if (!adapter) continue;
        const full = join(dir, name);
        let realPath: string;
        try {
          if (!statSync(full).isFile()) continue;
          realPath = realpathSync(full);
        } catch {
          continue;
        }
        seen.add(name);
        if (takenPaths.has(realPath)) continue;
        const meta = SUPPORTED_AGENTS.find((s) => s.adapter === adapter);
        const candidate: DiscoveredAgent = {
          id: name,
          adapter,
          command: full,
          ...(meta?.label ? { label: meta.label } : {}),
          ...(meta?.icon ? { icon: meta.icon } : {}),
        };
        const previous = found.get(realPath);
        if (!previous || (previous.adapter === adapter && discoveryRank(adapter, name) < discoveryRank(adapter, previous.id))) {
          found.set(realPath, candidate);
        }
      }
    }
    return [...found.values()].sort((a, b) => a.id.localeCompare(b.id));
  }

  /**
   * Adds what discovery finds on this machine, as the page's one-click entries do: another copy of
   * a known CLI on PATH, and a signed-in account no instance links yet. What the person removed
   * stays out for good. A failing or odd-looking find is skipped, never fatal to the rest.
   *
   * A registry the hub does not own — an inline `CALLI_AGENTS_JSON` list, or a read-only test
   * registry — joins nothing: a test or script machine must not pick up this machine's real CLIs
   * and accounts. The page's own 「发现/添加」 entries keep working on it all the same.
   */
  async autoJoin(): Promise<AgentInstance[]> {
    if (!this.file) return [];
    const added: AgentInstance[] = [];
    for (const found of await this.discover()) {
      if (this.removed.paths.includes(realPathOf(found.command))) continue;
      try {
        added.push(await this.add({ id: this.freeId(found.id), adapter: found.adapter, command: [found.command] }));
      } catch (error) {
        console.warn(`warning: could not auto-join ${found.id}: ${error instanceof Error ? error.message : error}`);
      }
    }
    for (const account of await this.discoverAccounts()) {
      // Any env entry matching a removed one names the same account the person put away.
      if (Object.entries(account.env).some(([k, v]) => this.removed.envs.includes(`${k}=${v}`))) continue;
      try {
        added.push(await this.add({
          id: this.freeId(account.id),
          adapter: account.adapter,
          label: account.label,
          env: account.env,
          account: account.email,
        }));
      } catch (error) {
        console.warn(`warning: could not auto-join ${account.email}: ${error instanceof Error ? error.message : error}`);
      }
    }
    return added;
  }

  /** Runs {@link autoJoin} now and then on a timer; `dispose` stops it. `CALLI_AUTO_JOIN=off`
   *  turns the joining off for anyone who wants a list that only changes by hand;
   *  `CALLI_LOCAL_SCAN=off` is the wider switch that also stops the machine's other scanners. */
  startAutoJoin(everyMs = 10 * 60_000, env: NodeJS.ProcessEnv = process.env): void {
    // A registry without a file joins nothing (see autoJoin), so no timer is started either.
    if (env.CALLI_AUTO_JOIN === 'off' || env.CALLI_LOCAL_SCAN === 'off' || !this.file) return;
    const run = () => void this.autoJoin().catch((error) =>
      console.warn(`warning: agent auto-join failed: ${error instanceof Error ? error.message : error}`));
    run();
    this.autoJoinTimer = setInterval(run, everyMs);
    this.autoJoinTimer.unref?.();
  }

  /** Records what a removed instance was, so a later discovery scan does not bring it straight
   *  back. An instance that runs an account is remembered by its env alone: its executable is the
   *  adapter's own, shared with every other instance of it, and blacklisting that path would keep
   *  plain CLI copies of the same binary out for good. A plain CLI copy is remembered by its
   *  executable's real path. */
  private rememberRemoved(instance: AgentInstance): void {
    const runsAccount = Object.keys(instance.env ?? {}).length > 0 || instance.account != null;
    if (!runsAccount) {
      const dirs = (process.env.PATH ?? '').split(delimiter).filter(Boolean);
      const command = instance.command ?? this.defaultCommands.get(instance.adapter) ?? [];
      for (const [i, token] of command.entries()) {
        if (i > 0 && !token.includes('/') && !token.includes('\\')) continue;
        const path = resolveCommandPath(token, dirs);
        if (!path) continue;
        const real = realPathOf(path);
        if (!this.removed.paths.includes(real)) this.removed.paths.push(real);
      }
    }
    for (const [key, value] of Object.entries(instance.env ?? {})) {
      const entry = `${key}=${value}`;
      if (!this.removed.envs.includes(entry)) this.removed.envs.push(entry);
    }
  }

  private async persistRemoved(): Promise<void> {
    const file = this.removedFile;
    if (!file) return;
    const body = JSON.stringify(this.removed, null, 2) + '\n';
    const fs = await import('node:fs/promises');
    const temporary = `${file}.${randomUUID()}.tmp`;
    try {
      await fs.writeFile(temporary, body, { encoding: 'utf8', mode: 0o600 });
      await fs.rename(temporary, file);
    } finally {
      await fs.rm(temporary, { force: true });
    }
  }

  /** The id asked for, or the next free numbered copy of it (`agy`, `agy-2`, `agy-3`…). */
  private freeId(want: string): string {
    if (!this.instances.has(want)) return want;
    const base = want.replace(/[._-]?\d+$/, '') || want;
    let n = 2;
    while (this.instances.has(`${base}-${n}`)) n += 1;
    return `${base}-${n}`;
  }
}

function basenameOf(command: unknown): string {
  if (!Array.isArray(command) || command.length === 0) return '';
  const raw = String(command[0]);
  return raw.split(/[/\\]/).pop() ?? '';
}

/** `base` itself, or a numbered copy such as `agy2` or `claude-2`, never `ping` or `traex-bridge`. */
function copyOf(base: string): RegExp {
  return new RegExp(`^${escapeRegExp(base)}(?:$|[._-]?\\d)`);
}

/** Lower is a better name for a group of aliases: the adapter's own name, then compatibility names. */
function discoveryRank(adapter: AgentInstance['adapter'], name: string): number {
  if (name === adapter) return 0;
  if (isNumberedCopy(name, adapter)) return 1;
  const preferred = PREFERRED_NAMES[adapter];
  const exact = preferred.indexOf(name);
  if (exact >= 0) return 10 + exact;
  const alias = preferred.findIndex((base) => isNumberedCopy(name, base));
  return alias >= 0 ? 30 + alias : 100;
}

function isNumberedCopy(name: string, base: string): boolean {
  return name.length > base.length && copyOf(base).test(name);
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Where a command token lives: an explicit path as given, or the first PATH directory with it. */
function resolveCommandPath(token: string, dirs: string[]): string | null {
  const candidates = isAbsolute(token) || token.includes('/') || token.includes('\\')
    ? [resolve(token)]
    : dirs.map((dir) => join(dir, token));
  for (const candidate of candidates) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      // keep looking
    }
  }
  return null;
}

function realPathOf(path: string): string {
  try {
    return realpathSync(path);
  } catch {
    return resolve(path);
  }
}
