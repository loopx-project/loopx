import { nativeQuestions } from './questions.js';
import type { WorkMode } from './types.js';
import { zcodeUsage } from './usage.js';
import { timedUsage } from './live-usage.js';
import { ZcodeUsageFile } from './zcode-usage.js';
import { cliDiagnostic } from './diagnostics.js';
import { fetchZcodeQuota } from './zcode-quota.js';
import { loadZcodeAccounts, type ZcodeAccounts } from './zcode-account.js';
import { goalSnapshot, goalSummary, parseGoalCommand, type GoalControl, type GoalSnapshot } from './native-goal.js';
import { isCompaction, trackCompaction } from './compaction.js';
import { ZCODE_ICON } from './icons.js';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, join } from 'node:path';
import { commandExists } from '../util.js';
import type { EmittedEvent } from '../events.js';
import type { UsageSample } from '../telemetry.js';
import { quoteArg } from './session-location.js';
import { expandEnvTemplate, probeMcpToolNames, type ConfiguredMcpServer } from './mcp-probe.js';
import { ZcodeWire } from './zcode-wire.js';
import { EFFORT_LEVELS, sessionEnvironment, type AgentAdapter, type AgentSession, type EffortLevel, type ModelOption, type StartOptions, type TurnHooks, type TurnInput, type TurnResult } from './types.js';

export const ZCODE_BASIC_TOOLS = ['Read', 'Write', 'Edit', 'Bash', 'Grep', 'Glob', 'AskUserQuestion', 'EnterPlanMode', 'ExitPlanMode'];
const DELIVERY = 'desktop-continuous';
const COMMANDS = [
  { name: 'goal', description: '设置原生目标并持续执行；可查看、暂停、继续或清除', hint: '[目标 | pause | resume | clear]' },
  { name: 'compact', description: '压缩当前会话上下文', hint: '[压缩说明]' },
  { name: 'help', description: '查看精简接入支持的命令' },
  { name: 'session', description: '查看当前会话、模型和记忆状态' },
  { name: 'usage', description: '查看会话 token 用量（非套餐剩余额度）' },
];

/**
 * MCP servers follow zcode's own plugin configuration, so installing one there (`zcode plugins
 * install …`) is all it takes to reach hub sessions too: the protocol app-server starts nothing
 * on its own, not even installed plugins, so the hub passes the definitions in session/create.
 * Sources: the plugin registry and enabled flags under ~/.zcode/cli, and each plugin's .mcp.json;
 * ${VAR} references resolve against the daemon's environment, keeping secrets out of files.
 */
export function readZcodeMcpServers(env: NodeJS.ProcessEnv): ConfiguredMcpServer[] {
  const root = join(env.ZCODE_DATA_BASE_DIR || homedir(), '.zcode', 'cli');
  let registry: any;
  let enabled: Record<string, unknown>;
  try {
    registry = JSON.parse(readFileSync(join(root, 'plugins', 'installed_plugins.json'), 'utf8'));
    enabled = JSON.parse(readFileSync(join(root, 'config.json'), 'utf8'))?.plugins?.enabledPlugins ?? {};
  } catch {
    return []; // zcode has no plugin state here: nothing to pass through.
  }
  const servers: ConfiguredMcpServer[] = [];
  for (const plugin of Array.isArray(registry?.plugins) ? registry.plugins : []) {
    const id = typeof plugin?.id === 'string' ? plugin.id : '';
    const installPath = typeof plugin?.installPath === 'string' ? plugin.installPath : '';
    if (!id || !installPath || enabled[id] === false) continue;
    let declared: unknown;
    try {
      declared = JSON.parse(readFileSync(join(installPath, '.mcp.json'), 'utf8'))?.mcpServers;
    } catch {
      continue; // A plugin without MCP definitions contributes none.
    }
    if (!declared || typeof declared !== 'object') continue;
    for (const [name, entry] of Object.entries(declared as Record<string, any>)) {
      if (entry?.type !== undefined && entry.type !== 'stdio') continue; // the hub passes stdio only
      const command = typeof entry?.command === 'string' ? expandEnvTemplate(entry.command, env) : null;
      if (!/^[A-Za-z0-9_-]+$/.test(name) || !command) continue;
      const args: string[] = [];
      let valid = Array.isArray(entry.args);
      if (valid) for (const arg of entry.args) {
        const expanded = typeof arg === 'string' ? expandEnvTemplate(arg, env) : null;
        if (expanded === null) { valid = false; break; }
        args.push(expanded);
      }
      const variables: Record<string, string> = {};
      if (valid && entry.env && typeof entry.env === 'object') for (const [key, value] of Object.entries(entry.env)) {
        const expanded = typeof value === 'string' ? expandEnvTemplate(value, env) : null;
        if (expanded === null) { valid = false; break; }
        variables[key] = expanded;
      }
      if (valid) servers.push({ name, command, args, env: variables });
      else console.warn(`[zcode] MCP server ${name} skipped: invalid shape or unresolved \${VAR}`);
    }
  }
  return servers;
}

/** Probed tool names per server config, cached for the daemon's lifetime. */
const mcpToolCache = new Map<string, Promise<string[]>>();

function zcodeMcpToolNames(servers: ConfiguredMcpServer[]): Promise<string[]> {
  const key = JSON.stringify(servers);
  let probed = mcpToolCache.get(key);
  if (!probed) {
    probed = Promise.all(servers.map((server) => probeMcpToolNames(server))).then((lists) => lists.flat());
    mcpToolCache.set(key, probed);
  }
  return probed;
}

function selection(id: string, effort?: EffortLevel): { providerId: string; modelId: string; options?: { reasoningLevel: string } } {
  try {
    const [providerId, modelId, defaultEffort] = JSON.parse(id);
    const reasoningLevel = effort ?? defaultEffort;
    if (typeof providerId === 'string' && providerId && typeof modelId === 'string' && modelId) return { providerId, modelId, ...(typeof reasoningLevel === 'string' && reasoningLevel ? { options: { reasoningLevel } } : {}) };
  } catch { /* Invalid opaque model id. */ }
  throw new Error('无效的 zcode 模型，请从模型列表选择');
}

export function zcodeModels(snapshot: any): ModelOption[] {
  const current = snapshot?.settings?.model?.current;
  return (snapshot?.settings?.model?.available ?? []).filter((m: any) => m.ref?.providerId && m.ref?.modelId && !m.disabledReason).map((m: any) => ({
    id: JSON.stringify([m.ref.providerId, m.ref.modelId, m.reasoning?.defaultLevel ?? m.ref.options?.reasoningLevel ?? null]), label: m.label || m.ref.modelId,
    description: m.description,
    efforts: (m.reasoning?.levels ?? []).map((l: any) => l.value).filter((l: any) => EFFORT_LEVELS.includes(l)),
    ...(typeof m.properties?.inputFormat?.supportsImage === 'boolean' ? { images: m.properties.inputFormat.supportsImage } : {}),
    isDefault: current?.providerId === m.ref.providerId && current?.modelId === m.ref.modelId,
  }));
}

/** Native current-context measurement, never the session's cumulative billed tokens. */
export function zcodeContextUsage(snapshot: any): EmittedEvent | null {
  const usage = snapshot?.runtime?.contextUsage;
  if (typeof usage?.used !== 'number' || !Number.isFinite(usage.used) || usage.used < 0
    || typeof usage.size !== 'number' || !Number.isFinite(usage.size) || usage.size <= 0) return null;
  const cost = usage.cost;
  const validCost = typeof cost?.amount === 'number' && Number.isFinite(cost.amount) && cost.amount >= 0
    && typeof cost.currency === 'string' && cost.currency.trim();
  return { type: 'usage.updated', data: { used: usage.used, size: usage.size, ...(validCost ? { cost: { amount: cost.amount, currency: cost.currency } } : {}) } };
}

export function mapZcodeEvent(event: any): EmittedEvent | null {
  const p = event.payload ?? {};
  // The legacy protocol preserves the native compaction payload under session.updated.
  if (event.type === 'session.updated' && typeof p.operationId === 'string' && p.display === 'separator'
    && ['manual', 'auto', 'partial', 'reactive', 'session_memory'].includes(p.trigger)
    && ['started', 'retrying', 'completed', 'failed', 'interrupted', 'skipped'].includes(p.status)) {
    return { type: 'context.compaction', data: { id: p.operationId,
      status: ['started', 'retrying'].includes(p.status) ? 'in_progress' : p.status,
      automatic: ['auto', 'reactive', 'session_memory'].includes(p.trigger),
    } };
  }
  if (event.type === 'model.streaming' && (p.kind === 'text_delta' || p.kind === 'reasoning_delta') && typeof p.delta === 'string') {
    return { type: p.kind === 'text_delta' ? 'message.delta' : 'reasoning.delta', data: { text: p.delta } };
  }
  if (event.type !== 'tool.updated' || !p.toolCallId) return null;
  const name = String(p.toolName ?? '');
  const kind = ({ Read: 'read', Write: 'edit', Edit: 'edit', Bash: 'execute', Grep: 'search', Glob: 'search' } as Record<string, string>)[name] ?? 'other';
  if (p.kind === 'scheduled') return { type: 'tool.start', data: { id: p.toolCallId, title: p.input?.file_path ?? p.input?.path ?? p.input?.command ?? name, kind, input: p.input, status: 'in_progress', ...(name ? { tool: name } : {}) } };
  if (p.kind === 'progress') return { type: 'tool.update', data: { id: p.toolCallId, status: 'in_progress', output: [p.stdoutTail, p.stderrTail].filter(Boolean).join('\n') } };
  if (p.kind === 'result' || p.kind === 'error') return { type: 'tool.end', data: { id: p.toolCallId, status: p.kind === 'error' || p.result?.success === false ? 'failed' : 'completed', output: p.error?.message ?? (typeof p.result?.output === 'string' ? p.result.output : JSON.stringify(p.result?.output ?? p.result ?? '')) } };
  return null;
}

export const ZCODE_METADATA = {
  adapter: 'zcode' as const,
  label: 'ZCode',
  icon: ZCODE_ICON,
};

export class ZcodeAdapter implements AgentAdapter {
  readonly id = 'zcode';
  readonly label = ZCODE_METADATA.label;
  readonly icon = ZCODE_METADATA.icon;
  readonly capabilities = {
    workModes: ['execute', 'plan'] as const,
    goal: { controls: ['pause'] as const, actions: ['set', 'show', 'pause', 'resume', 'clear'] as const, live: true },
    coordination: true, resume: true, modelSwitch: true, approvals: true,
    // ZCode also treats toolAllowlist as approval exemptions, so permission tiers would mislead.
    permissionModes: [], effortLevels: [],
    images: true, deleteSession: false, history: false, quota: true,
    compact: true, fork: false, forkAt: false, multiAccount: false, forkInto: false,
  };
  private cachedModels: ModelOption[] | null = null;
  private modelsCheckedAt = 0;
  private probing: Promise<ModelOption[]> | null = null;
  private readonly command: string[];
  constructor(command: string[] = [], private readonly env: Record<string, string> = {}) {
    const base = command.length ? command : ['zcode'];
    this.command = base.includes('app-server') || base.includes('agent-server') ? [...base] : [...base, 'app-server'];
  }
  diagnose() { return cliDiagnostic(this.command, this.env, 'zcode NDJSON'); }

  /** --no-browser leaves the OAuth URL on screen for a terminal that is not on the browser's host. */
  loginCommand(): string {
    const keys = ['HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME'];
    const env = keys.flatMap((key) => (this.env[key] ? [`${key}=${quoteArg(this.env[key])}`] : [])).join(' ');
    return `${env ? `env ${env} ` : ''}${quoteArg(this.command[0])} login --no-browser`;
  }

  async available(): Promise<boolean> { return commandExists(this.command[0]); }
  fetchQuota() { return fetchZcodeQuota(this.env); }
  commands() { return Promise.resolve(COMMANDS); }
  // Native terminal sessions do not inherit calli's runtime preferences.
  resumeCommand(id: string): string { return `zcode --resume ${quoteArg(id)}`; }
  async models(refresh = false): Promise<ModelOption[]> {
    if (refresh) this.cachedModels = null;
    if (this.cachedModels && Date.now() - this.modelsCheckedAt < 60_000) return this.cachedModels;
    if (this.probing) return this.probing;
    this.probing = (async () => {
      const session = await this.open({ cwd: process.cwd() }, true);
      try { return this.cachedModels ?? []; } finally { await session.close(); }
    })();
    try { return await this.probing; } finally { this.probing = null; }
  }
  start(opts: StartOptions): Promise<AgentSession> { return this.open(opts, false); }
  private async open(opts: StartOptions, probe: boolean): Promise<ZcodeSession> {
    const env = sessionEnvironment(this.env, opts.coordinationEnv);
    const accounts = await loadZcodeAccounts(env, opts.cwd);
    const wire = new ZcodeWire(this.command, opts.cwd, { ...env, ...accounts?.environment });
    const session = new ZcodeSession(wire, probe, accounts, env, opts.cwd);
    try {
      if (accounts) {
        const result = await wire.request('provider/updateAccountConfig', accounts.overlay);
        if (result?.receivedRevision !== accounts.overlay.revision) throw new Error('zcode 未确认账号模型同步，请升级 zcode');
      }
      const snapshot = await session.open(opts);
      this.cachedModels = zcodeModels(snapshot);
      this.modelsCheckedAt = Date.now();
      return session;
    } catch (error) { wire.close(); throw error; }
  }
}

class ZcodeSession implements AgentSession {
  private workMode: WorkMode = 'execute';
  private onWorkMode?: (mode: WorkMode) => void;
  agentSessionId = '';
  private preferenceRequests = 0;
  private active: { inputId: string; turnId?: string; goal?: boolean; compact?: boolean; error?: string; hooks: TurnHooks; resolve(result: TurnResult): void; reject(error: Error): void; text: string; seen: Set<string> } | null = null;
  private closed = false;
  private goalActive = false;
  /** The last target the server reported, kept so the goal card can read it any time. */
  private target: unknown = null;
  private onGoal?: (goal: GoalSnapshot | null) => void;
  /** Remembers a target the server just reported and tells the hub when it changed. */
  private noteTarget(target: unknown): void {
    this.goalActive = (target as { status?: string } | null)?.status === 'active';
    if (JSON.stringify(goalSnapshot(target)) === JSON.stringify(goalSnapshot(this.target))) return;
    this.target = target;
    this.onGoal?.(goalSnapshot(target));
  }
  private submission: Promise<void> | null = null;
  private preparing: Promise<void> | null = null;
  private usage: EmittedEvent | null = null;
  private usageId = 0;
  private accounting: any = null;
  constructor(private readonly wire: ZcodeWire, private readonly probe: boolean, private readonly accounts: ZcodeAccounts | null, private readonly env: NodeJS.ProcessEnv, private readonly cwd: string) {
    wire.onEvent = (method, params) => this.event(method, params);
    wire.onRequest = (method, params) => this.request(method, params);
    wire.onClosed = (error) => { this.closed = true; this.active?.reject(error); this.active = null; };
  }
  async open(opts: StartOptions): Promise<any> {
    this.onWorkMode = opts.onWorkMode;
    this.onGoal = opts.onGoal;
    this.workMode = opts.workMode ?? 'execute';
    const workspace = { workspacePath: opts.cwd, workspaceKey: opts.cwd };
    const model = opts.model ? selection(opts.model, opts.effort) : undefined;
    const thoughtLevel = opts.effort ?? model?.options?.reasoningLevel;
    // MCP servers from the machine-local config, if any; their probed tool names widen the
    // allowlist, which matches exact names only.
    const mcpServers = readZcodeMcpServers(this.env);
    const mcpTools = mcpServers.length ? await zcodeMcpToolNames(mcpServers) : [];
    const restrictions = {
      workspace,
      // Real zcode validates mcpServers with a strict schema: name, command, args and env are
      // all required — an empty argument list is `args: []`, never a missing field.
      mcpServers: mcpServers.map((server) => ({ name: server.name, command: server.command, args: server.args, env: Object.entries(server.env).map(([name, value]) => ({ name, value })) })),
      toolAllowlist: mcpTools.length ? [...ZCODE_BASIC_TOOLS, ...mcpTools] : ZCODE_BASIC_TOOLS,
      dynamicWorkflowEnabled: false, offPeakToolEnabled: false,
    };
    const snapshot = await this.wire.request(opts.resumeId ? 'session/resume' : 'session/create', opts.resumeId
      ? { ...restrictions, sessionId: opts.resumeId }
      : { ...restrictions, mode: this.workMode === 'plan' ? 'plan' : 'build', persistence: this.probe ? 'deferred' : 'immediate', titleGenerationEnabled: false, ...(model ? { model } : {}), ...(thoughtLevel ? { thoughtLevel } : {}) });
    if (!this.preferenceRequests) throw new Error('此 zcode 未请求运行时偏好，无法保证关闭记忆，请升级 zcode');
    if (typeof snapshot?.session?.sessionId !== 'string') throw new Error('zcode 未返回会话 ID');
    this.agentSessionId = snapshot.session.sessionId;
    this.usage = zcodeContextUsage(snapshot);
    if (!this.probe && zcodeModels(snapshot).length === 0) {
      await this.close();
      throw new Error('zcode 没有可用模型。请检查 API 模型配置，或在 zcode 中登录 BigModel / Z.ai 个人 Coding Plan 后刷新模型列表；其他账号套餐模型暂未支持。');
    }
    this.noteTarget(snapshot.session.target ?? snapshot.projection?.target);
    if (this.workMode === 'plan' && this.goalActive) throw new Error('请先暂停当前目标，再进入计划模式');
    if (opts.resumeId) {
      await this.wire.request('session/setMode', { sessionId: this.agentSessionId, mode: this.workMode === 'plan' ? 'plan' : 'build' });
      if (opts.model) await this.setModel(opts.model);
    }
    if (opts.effort) await this.setEffort(opts.effort);
    if (!this.probe) {
      this.accounting = await this.wire.request('session/usage', { sessionId: this.agentSessionId }, 3000, false).catch(() => null);
      await this.wire.request('session/subscribe', { sessionId: this.agentSessionId, deliveryKind: DELIVERY, includeSnapshot: false });
    }
    return snapshot;
  }
  async setWorkMode(mode: WorkMode): Promise<void> {
    if (mode === this.workMode) return;
    if (mode === 'plan' && this.goalActive) throw new Error('请先暂停当前目标，再进入计划模式');
    await this.wire.request('session/setMode', { sessionId: this.agentSessionId, mode: mode === 'plan' ? 'plan' : 'build' });
    this.workMode = mode;
  }

  async setModel(model: string): Promise<boolean> {
    const snapshot = await this.wire.request('session/setModel', { sessionId: this.agentSessionId, model: selection(model), persistAsWorkspaceLastUsed: false });
    this.usage = zcodeContextUsage(snapshot);
    return true;
  }
  async setEffort(effort: EffortLevel): Promise<boolean> {
    await this.wire.request('session/setThoughtLevel', { sessionId: this.agentSessionId, thoughtLevel: effort, persistAsWorkspaceLastUsed: false }); return true;
  }
  async prompt(input: TurnInput, hooks: TurnHooks): Promise<TurnResult> {
    let prepared!: () => void;
    this.preparing = new Promise(resolve => { prepared = resolve; });
    if (this.usage) hooks.emit(this.usage);
    const compact = isCompaction(input.text);
    const run = async () => {
      const measure = !input.text.startsWith('/') || compact || /^\/goal\s+/.test(input.text);
      let since = Date.now(), reported = false;
      let pending: Promise<void> | null = null;
      // Establish this turn's baseline so terminal activity between hub turns is not charged here.
      if (measure && !this.closed) this.accounting = await this.wire.request('session/usage', { sessionId: this.agentSessionId }, 3000, false).catch(() => null);
      const file = new ZcodeUsageFile(this.env, this.cwd, this.agentSessionId);
      const adjustedSummary = this.accounting && 'inputBaselineBySource' in this.accounting;
      const useFile = measure && (adjustedSummary || !this.accounting) && file.start();
      const emit = (sample: UsageSample) => hooks.emit({ type: 'usage.sample', data: { ...sample } });
      const read = (final = false): Promise<void> => {
        if (pending) return pending;
        pending = (async () => {
          if (useFile) { file.read(emit); return; }
          if (adjustedSummary) return; // This protocol's input totals are not consumption; keep them unknown.
          const after = await this.wire.request('session/usage', { sessionId: this.agentSessionId }, 3000, false).catch(() => null);
          if (!after) return; // Keep the last good baseline across transient failures within this turn.
          const sample = zcodeUsage(this.accounting, after);
          this.accounting = after;
          if (sample.total !== undefined && (sample.total > 0 || final && !reported)) {
            const end = Date.now();
            hooks.emit({ type: 'usage.sample', data: { ...timedUsage({ id: `usage-${++this.usageId}`, scope: 'update', source: 'zcode.session/usage', ...sample }, since, end) } });
            since = end; reported = true;
          }
        })().finally(() => { pending = null; });
        return pending;
      };
      const timer = measure ? setInterval(() => { if (!this.closed) void read(); }, 2000) : undefined;
      timer?.unref();
      try {
        const turn = this.promptInternal(input, hooks);
        prepared();
        return await turn;
      }
      finally {
        prepared();
        clearInterval(timer);
        await pending;
        if (measure && (!this.closed || useFile)) await read(true);
        file.close();
        await this.refreshUsage(hooks, compact);
      }
    };
    return compact ? trackCompaction(hooks, run) : run();
  }

  private async refreshUsage(hooks: TurnHooks, invalidateOnFailure: boolean): Promise<void> {
    if (this.closed) return;
    const previous = this.usage;
    try {
      const snapshot = await this.wire.request('session/read', { sessionId: this.agentSessionId, messageLimit: 1 }, 3000, false);
      this.usage = zcodeContextUsage(snapshot);
    } catch {
      // Statistics are optional; a failed read must not turn a successful reply into a failed turn.
      if (!invalidateOnFailure) return;
      this.usage = null;
    }
    if (this.usage) hooks.emit(this.usage);
    else if (previous) hooks.emit({ type: 'usage.updated', data: { used: null, size: previous.data.size } });
  }

  private async promptInternal(input: TurnInput, hooks: TurnHooks): Promise<TurnResult> {
    if (this.closed) throw new Error('zcode 连接已关闭');
    if (this.active) throw new Error('zcode 已有正在运行的轮次');
    const text = input.text.trim();
    const match = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(text);
    const name = match?.[1];
    if (name && input.images.length) throw new Error(`/${name} 不接受图片，请将图片作为普通消息发送。`);
    const goal = parseGoalCommand(text);
    const compact = name === 'compact';
    if (text.startsWith('/') && !COMMANDS.some((command) => command.name === name)) throw new Error('精简接入不执行此命令，请用 /help 查看支持的命令');
    if (name && !compact && !goal) {
      if (match?.[2]) throw new Error(`/${name} 不接受参数`);
      let response: string;
      if (name === 'help') {
        response = COMMANDS.map((command) => `/${command.name}：${command.description}`).join('\n\n');
      } else if (name === 'session') {
        const snapshot = await this.wire.request('session/read', { sessionId: this.agentSessionId, messageLimit: 1 }, 3000, false);
        const model = snapshot?.settings?.model?.current;
        response = [`会话：${this.agentSessionId}`, `模型：${model?.providerId ?? '未知'} / ${model?.modelId ?? '未知'}`, '自动记忆：关闭', '工具范围：基础编码工具', '套餐额度：请在左下角额度看板查看（默认账号）'].join('\n\n');
      } else {
        const usage = await this.wire.request('session/usage', { sessionId: this.agentSessionId });
        const count = (key: string) => typeof usage?.[key] === 'number' && Number.isFinite(usage[key]) && usage[key] >= 0 ? usage[key].toLocaleString('en-US') : '未知';
        response = ['当前会话累计用量', `总计：${count('totalTokens')} tokens`, `输入：${count('inputTokens')} · 输出：${count('outputTokens')}`, `推理：${count('reasoningTokens')}`, `缓存读取：${count('cacheReadTokens')} · 缓存写入：${count('cacheCreationTokens')}`, `模型请求：${count('modelRequestCount')} · 错误：${count('modelErrorCount')}`, '套餐额度请在左下角额度看板查看；此处仅为会话 token 用量。'].join('\n\n');
      }
      hooks.emit({ type: 'message.delta', data: { text: response } });
      return { stopReason: 'end_turn' };
    }
    const inputId = randomUUID();
    return new Promise<TurnResult>((resolve, reject) => {
      const active = { inputId, hooks, resolve, reject, compact, goal: !!goal || (!compact && this.goalActive), text: '', seen: new Set<string>() };
      this.active = active;
      this.submission = this.wire.request(goal ? 'session/goal' : compact ? 'session/compact' : 'session/send', {
        sessionId: this.agentSessionId, inputId,
        ...(goal ? goal : compact ? { instructions: text.slice('/compact'.length).trim() } : {
          content: input.text,
          ...(input.images.length ? { attachments: input.images.map((image, index) => ({
            kind: 'image', mimeType: image.mimeType,
            filename: image.path ? basename(image.path) : `image-${index + 1}.${image.mimeType.split('/')[1]}`,
            ...(image.path ? { localPath: image.path } : { dataBase64: image.data }),
          })) } : {}),
        }),
      }).then((result) => {
        if (goal && this.active === active) {
          if (typeof result?.startedTurn !== 'boolean' || !result.snapshot) throw new Error('zcode 未返回可识别的目标状态');
          this.noteTarget(result.snapshot.session?.target ?? result.snapshot.projection?.target);
          if (!result.startedTurn) {
            hooks.emit({ type: 'message.delta', data: { text: goalSummary(result.snapshot.session?.target ?? result.snapshot.projection?.target) } });
            this.finish({ stopReason: 'end_turn' });
          }
        }
        if (compact && result?.compact?.state === 'already_running' && this.active === active) {
          this.finish({ stopReason: 'error', error: 'zcode 已在压缩，请稍后重试' });
          return;
        }
        if (compact && !result?.compact && this.active === active) {
          if (typeof result?.response !== 'string' || !result?.snapshot) throw new Error('zcode 未返回可识别的压缩状态');
          if (result?.response) hooks.emit({ type: 'message.delta', data: { text: result.response } });
          this.finish({ stopReason: 'end_turn' });
        }
      }).catch(async (error) => {
        // Setting a native target may persist it before model admission fails.
        if (goal?.action === 'set' || goal?.action === 'resume') {
          await this.wire.request('session/goal', { sessionId: this.agentSessionId, action: 'pause' }).catch(() => undefined);
        }
        if (this.active === active) { this.active = null; reject(error); }
      });
    });
  }
  async cancel(): Promise<void> {
    await this.preparing;
    const active = this.active;
    if (!active) return;
    // Stop must follow admission; an earlier stop can miss a not-yet-started turn.
    await this.submission;
    if (this.active !== active) return;
    await this.wire.request('session/stop', { sessionId: this.agentSessionId });
  }
  async controlGoal(action: GoalControl): Promise<string> {
    if (action !== 'pause') throw new Error('请先停止当前执行，再查看或清除目标。');
    await this.preparing;
    await this.submission;
    await this.wire.request('session/goal', { sessionId: this.agentSessionId, action: 'pause' });
    await this.refreshGoal();
    return '目标已暂停。';
  }
  /** Reads the session's target fresh; the last answer stands when the wire is gone. */
  async goalState(): Promise<GoalSnapshot | null> {
    if (!this.closed && this.agentSessionId) await this.refreshGoal();
    return goalSnapshot(this.target);
  }
  private async refreshGoal(): Promise<void> {
    const snapshot = await this.wire.request('session/read', { sessionId: this.agentSessionId, messageLimit: 1 }, 3000, false).catch(() => null);
    if (snapshot) this.noteTarget(snapshot.session?.target ?? snapshot.projection?.target);
  }
  async close(): Promise<void> {
    try { if (!this.closed && this.agentSessionId) await this.wire.request('session/close', { sessionId: this.agentSessionId, ...(this.probe ? { expectedPersistence: 'deferred' } : {}) }, 5_000); }
    finally { this.wire.close(); }
  }
  private finish(result: TurnResult): void { const active = this.active; this.active = null; active?.resolve(result); }
  private event(method: string, event: any): void {
    const active = this.active;
    // The legacy protocol maps native SessionModeChanged to session.updated.
    if (method === 'session/event' && event?.sessionId === this.agentSessionId
      && (event.type === 'session_mode_changed' || event.type === 'session.updated' && typeof event.payload?.planEnabled === 'boolean')) {
      const mode = (event.payload?.planEnabled ?? event.payload?.mode === 'plan') ? 'plan' : 'execute';
      if (mode !== this.workMode) { this.workMode = mode; this.onWorkMode?.(mode); }
    }
    if (method === 'state.updated' && active?.goal && event?.sessionId === this.agentSessionId
      && ['goal_continuation_completed', 'goal_continuation_failed', 'prompt_completed', 'prompt_failed'].includes(event.reason)) {
      // Native continuation owns all its turns, including verification between turns.
      void this.wire.request('session/read', { sessionId: this.agentSessionId, messageLimit: 1 }).then((snapshot) => {
        if (this.active !== active) return;
        const target = snapshot.session?.target ?? snapshot.projection?.target;
        this.noteTarget(target);
        active.hooks.emit({ type: 'message.delta', data: { text: `\n\n${goalSummary(target)}` } });
        this.finish(event.reason.endsWith('_failed') ? { stopReason: 'error', error: active.error ?? 'zcode 目标执行失败' }
          : { stopReason: target?.status === 'paused' ? 'cancelled' : 'end_turn' });
      }).catch((error) => { if (this.active === active) { this.active = null; active.reject(error); } });
      return;
    }
    if (method !== 'session/event' || !active || event?.sessionId !== this.agentSessionId) return;
    if (event.eventId) { if (active.seen.has(event.eventId)) return; active.seen.add(event.eventId); }
    const p = event.payload ?? {};
    if (event.type === 'turn.started') {
      if (!active.goal && p.inputId !== active.inputId) return;
      if (active.goal && active.turnId && active.turnId !== event.turnId) active.hooks.emit({ type: 'message.delta', data: { text: '\n\n' } });
      active.turnId = event.turnId;
      active.text = '';
    }
    const terminal = event.type === 'turn.completed' || event.type === 'turn.failed';
    if ((!active.turnId || event.turnId !== active.turnId) && !(terminal && p.inputId === active.inputId)) return;
    const mapped = mapZcodeEvent(event);
    if (mapped?.type === 'context.compaction' && active.compact) return;
    if (mapped) { active.hooks.emit(mapped); if (mapped.type === 'message.delta') active.text += mapped.data.text; }
    if (event.type === 'turn.completed') {
      if (!active.text && p.response) active.hooks.emit({ type: 'message.delta', data: { text: p.response } });
      if (active.goal) return;
      this.finish(p.resultType === 'success' ? { stopReason: 'end_turn' } : p.resultType === 'cancelled' ? { stopReason: 'cancelled' } : { stopReason: 'error', error: `zcode：${p.resultType}` });
    } else if (event.type === 'turn.failed') {
      const error = p.error?.message ?? 'zcode 执行失败';
      if (active.goal) active.error = error;
      else this.finish({ stopReason: 'error', error });
    }
  }
  private async request(method: string, params: any): Promise<unknown> {
    if (method === 'session/requestRuntimePreferences') {
      this.preferenceRequests++;
      return { memoryEnabled: false, nativeSearchEnhancementsEnabled: false, askUserQuestionAutoResolutionEnabled: false };
    }
    if (method === 'interaction/requestProviderRuntimeHeaders') return this.accounts
      ? this.accounts.authorize(params, this.agentSessionId)
      : { headersApplied: false, errorMessage: '未找到 zcode 账号凭据；请在 zcode 中登录 BigModel / Z.ai 个人 Coding Plan，或配置 API 模型' };
    if (method === 'interaction/requestUserInput') {
      const active = this.active;
      if (!active?.hooks.requestInput || params?.sessionId !== this.agentSessionId) return { action: 'cancel' };
      const questions = nativeQuestions(params.questions);
      if (!questions.length) return { action: 'cancel' };
      const plan = params.schema?.interaction === 'plan_approval';
      if (plan) for (const q of questions) for (const option of q.options) if (option.value === 'approve') option.label = '开始执行';
      const answer = await active.hooks.requestInput({ id: params.requestId, title: plan ? '计划已准备好' : params.prompt || '需要你的意见', kind: plan ? 'plan' : 'question', plan: plan ? params.input?.plan : undefined, questions });
      if (active !== this.active || 'cancelled' in answer) return { action: 'cancel' };
      return { action: 'accept', content: { answers: Object.fromEntries(questions.map(q => [q.question, answer.answers[q.id].join(', ')])) } };
    }
    if (method !== 'interaction/requestPermission') return undefined;
    const active = this.active;
    if (!active || params?.sessionId !== this.agentSessionId) return { decision: 'deny' };
    const options = (params.options ?? []).filter((o: any) => typeof o.optionId === 'string' && o.response);
    const answer = await active.hooks.requestApproval({ title: params.reason || params.toolName || 'zcode 请求批准', options: options.map((o: any) => ({ optionId: o.optionId, name: o.name, kind: o.kind })), toolCall: { title: params.toolName, rawInput: params.input } });
    if (active !== this.active || 'cancelled' in answer) return { decision: 'deny' };
    return options.find((o: any) => o.optionId === answer.optionId)?.response ?? { decision: 'deny' };
  }
}
